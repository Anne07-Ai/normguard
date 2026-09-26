"""Production adapters for the C0 and C4 experiment configurations."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any, Protocol

from .contracts import PolicyClass, PolicyRule, SpanAction, SpanTrace
from .normalisation import NormalisationResult, ProtectedNormaliser

_WHITESPACE = re.compile(r"\s+")
_MASKED_CLASSES = {
    PolicyClass.EXACT_PRESERVE,
    PolicyClass.APPROVED_ALIAS,
    PolicyClass.REVIEW_OR_ABSTAIN,
}


@dataclass(frozen=True, slots=True)
class AdapterMetadata:
    """Stable identity and dependency metadata for an adapter."""

    configuration_id: str
    engine: str
    engine_version: str
    model: str | None = None
    model_version: str | None = None
    disabled_components: tuple[str, ...] = ()


class NormalisationAdapter(Protocol):
    """Common interface implemented by experiment normalisation adapters."""

    @property
    def metadata(self) -> AdapterMetadata: ...

    def normalise(self, text: str) -> NormalisationResult: ...


class C0Adapter:
    """Minimal NFC, whitespace and Unicode-casefold baseline."""

    _metadata = AdapterMetadata(
        configuration_id="C0",
        engine="python-unicodedata",
        engine_version=unicodedata.unidata_version,
    )

    @property
    def metadata(self) -> AdapterMetadata:
        return self._metadata

    def normalise(self, text: str) -> NormalisationResult:
        return ProtectedNormaliser(()).normalise(text)


class C4SpacyAdapter:
    """Policy-first, context-aware English spaCy lemmatisation adapter."""

    _disabled_components = ("parser", "ner")

    def __init__(
        self,
        rules: Sequence[PolicyRule],
        *,
        nlp: Any | None = None,
    ) -> None:
        self._normaliser = ProtectedNormaliser(rules)
        self._nlp = nlp or self._load_model()
        self._metadata = AdapterMetadata(
            configuration_id="C4",
            engine="spacy",
            engine_version=version("spacy"),
            model="en_core_web_sm",
            model_version=version("en-core-web-sm"),
            disabled_components=self._disabled_components,
        )

    @classmethod
    def _load_model(cls) -> Any:
        import spacy

        return spacy.load("en_core_web_sm", disable=list(cls._disabled_components))

    @property
    def metadata(self) -> AdapterMetadata:
        return self._metadata

    def normalise(self, text: str) -> NormalisationResult:
        matches = self._normaliser._select_policy_matches(text)
        masked = list(text)
        for match in matches:
            if match.rule.policy_class in _MASKED_CLASSES:
                masked[match.start : match.end] = ["x"] * (match.end - match.start)

        doc = self._nlp("".join(masked))
        tokens = tuple(token for token in doc if not token.is_space)
        chunks: list[str] = []
        traces: list[SpanTrace] = []
        cursor = 0
        token_index = 0
        review_required = False

        for match in matches:
            token_index = self._emit_tokens(
                text, tokens, token_index, cursor, match.start, chunks, traces
            )
            source = text[match.start : match.end]
            rule = match.rule
            if rule.policy_class is PolicyClass.CONTEXT_NORMALISABLE:
                emitted, pos, lemma, metadata = self._normalise_p2(
                    text, tokens, match.start, match.end
                )
                action = SpanAction.PRESERVED if emitted == source else SpanAction.TRANSFORMED
                normalised_surface = unicodedata.normalize("NFC", source).casefold()
            else:
                emitted, action, normalised_surface, lemma = self._normaliser._apply_policy(
                    source, rule
                )
                pos = None
                metadata = self._engine_metadata(masked=True)

            chunks.append(emitted)
            traces.append(
                SpanTrace(
                    text=source,
                    start=match.start,
                    end=match.end,
                    policy_class=rule.policy_class,
                    action=action,
                    emitted_text=emitted,
                    rule_id=rule.rule_id,
                    normalised_surface=normalised_surface,
                    part_of_speech=pos,
                    candidate_lemma=lemma,
                    engine_metadata=metadata,
                )
            )
            review_required |= rule.policy_class is PolicyClass.REVIEW_OR_ABSTAIN
            cursor = match.end
            while token_index < len(tokens) and tokens[token_index].idx < match.end:
                token_index += 1

        self._emit_tokens(text, tokens, token_index, cursor, len(text), chunks, traces)
        output = _WHITESPACE.sub(" ", "".join(chunks)).strip()
        return NormalisationResult(text, output, tuple(traces), review_required)

    def _emit_tokens(
        self,
        text: str,
        tokens: tuple[Any, ...],
        token_index: int,
        start: int,
        end: int,
        chunks: list[str],
        traces: list[SpanTrace],
    ) -> int:
        cursor = start
        while token_index < len(tokens):
            token = tokens[token_index]
            token_end = token.idx + len(token.text)
            if token.idx >= end:
                break
            if token.idx < start or token_end > end:
                token_index += 1
                continue
            chunks.append(unicodedata.normalize("NFC", text[cursor : token.idx]))
            source = text[token.idx:token_end]
            surface = unicodedata.normalize("NFC", source).casefold()
            lemma = unicodedata.normalize("NFC", token.lemma_ or surface).casefold()
            emitted = lemma
            chunks.append(emitted)
            traces.append(
                SpanTrace(
                    text=source,
                    start=token.idx,
                    end=token_end,
                    policy_class=PolicyClass.CONTEXT_NORMALISABLE,
                    action=(
                        SpanAction.PRESERVED if emitted == source else SpanAction.TRANSFORMED
                    ),
                    emitted_text=emitted,
                    normalised_surface=surface,
                    part_of_speech=token.pos_ or None,
                    candidate_lemma=lemma,
                    engine_metadata=self._engine_metadata(token=token),
                )
            )
            cursor = token_end
            token_index += 1
        chunks.append(unicodedata.normalize("NFC", text[cursor:end]))
        return token_index

    def _normalise_p2(
        self,
        text: str,
        tokens: tuple[Any, ...],
        start: int,
        end: int,
    ) -> tuple[str, str | None, str, Mapping[str, Any]]:
        covered = [
            token
            for token in tokens
            if start <= token.idx and token.idx + len(token.text) <= end
        ]
        if not covered:
            surface = unicodedata.normalize("NFC", text[start:end]).casefold()
            return surface, None, surface, self._engine_metadata()
        chunks: list[str] = []
        cursor = start
        for token in covered:
            chunks.append(unicodedata.normalize("NFC", text[cursor : token.idx]))
            chunks.append(unicodedata.normalize("NFC", token.lemma_ or token.text).casefold())
            cursor = token.idx + len(token.text)
        chunks.append(unicodedata.normalize("NFC", text[cursor:end]))
        lemmas = " ".join(token.lemma_.casefold() for token in covered)
        pos = " ".join(token.pos_ for token in covered) or None
        return "".join(chunks), pos, lemmas, self._engine_metadata(tokens=len(covered))

    def _engine_metadata(self, *, token: Any | None = None, **extra: Any) -> Mapping[str, Any]:
        metadata: dict[str, Any] = {
            "engine": self.metadata.engine,
            "engine_version": self.metadata.engine_version,
            "model": self.metadata.model,
            "model_version": self.metadata.model_version,
        }
        if token is not None:
            metadata["tokenizer_start"] = token.idx
            metadata["tokenizer_end"] = token.idx + len(token.text)
            metadata["tag"] = token.tag_
        metadata.update(extra)
        return metadata
