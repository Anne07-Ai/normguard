"""Deterministic policy-first text normalisation.

Policy matching always runs before morphology.  The morphology function is injected so
the safety layer stays engine-neutral and can be tested without third-party packages.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Pattern

from .contracts import PolicyClass, PolicyRule, SpanAction, SpanTrace

_TOKEN = re.compile(r"\w+(?:['’-]\w+)*", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class NormalisationResult:
    """Normalised text and the complete auditable span trace."""

    source_text: str
    output_text: str
    spans: tuple[SpanTrace, ...]
    review_required: bool


@dataclass(frozen=True, slots=True)
class _Match:
    start: int
    end: int
    rule: PolicyRule


class ProtectedNormaliser:
    """Apply policy rules before an injected token-level morphology function."""

    def __init__(
        self,
        rules: Sequence[PolicyRule],
        lemmatise: Callable[[str], str] | None = None,
    ) -> None:
        self._rules = tuple(rules)
        self._lemmatise = lemmatise or (lambda token: token)
        self._patterns = tuple((rule, self._compile(rule)) for rule in self._rules)

    @staticmethod
    def _compile(rule: PolicyRule) -> Pattern[str]:
        if rule.match_type == "regex":
            return re.compile(rule.pattern)
        literal = re.escape(rule.pattern)
        return re.compile(rf"(?<!\w){literal}(?!\w)")

    def normalise(self, text: str) -> NormalisationResult:
        """Return deterministic output while retaining offsets into the input text."""

        selected = self._select_policy_matches(text)
        chunks: list[str] = []
        traces: list[SpanTrace] = []
        cursor = 0
        review_required = False

        for match in selected:
            gap, gap_traces = self._normalise_unprotected(text, cursor, match.start)
            chunks.append(gap)
            traces.extend(gap_traces)

            source = text[match.start : match.end]
            emitted, action = self._apply_policy(source, match.rule)
            chunks.append(emitted)
            traces.append(
                SpanTrace(
                    text=source,
                    start=match.start,
                    end=match.end,
                    policy_class=match.rule.policy_class,
                    action=action,
                    emitted_text=emitted,
                    rule_id=match.rule.rule_id,
                )
            )
            review_required |= match.rule.policy_class is PolicyClass.REVIEW_OR_ABSTAIN
            cursor = match.end

        tail, tail_traces = self._normalise_unprotected(text, cursor, len(text))
        chunks.append(tail)
        traces.extend(tail_traces)
        output = _WHITESPACE.sub(" ", "".join(chunks)).strip()
        return NormalisationResult(text, output, tuple(traces), review_required)

    def _select_policy_matches(self, text: str) -> tuple[_Match, ...]:
        candidates = [
            _Match(match.start(), match.end(), rule)
            for rule, pattern in self._patterns
            for match in pattern.finditer(text)
            if match.end() > match.start()
        ]
        candidates.sort(
            key=lambda item: (
                item.start,
                -(item.end - item.start),
                item.rule.rule_id,
            )
        )

        selected: list[_Match] = []
        occupied_until = -1
        for candidate in candidates:
            if candidate.start >= occupied_until:
                selected.append(candidate)
                occupied_until = candidate.end
        return tuple(selected)

    def _normalise_unprotected(
        self,
        text: str,
        start: int,
        end: int,
    ) -> tuple[str, list[SpanTrace]]:
        segment = text[start:end]
        chunks: list[str] = []
        traces: list[SpanTrace] = []
        cursor = 0

        for token_match in _TOKEN.finditer(segment):
            chunks.append(unicodedata.normalize("NFC", segment[cursor : token_match.start()]))
            source = token_match.group()
            candidate = self._lemmatise(unicodedata.normalize("NFC", source).casefold())
            emitted = unicodedata.normalize("NFC", candidate).casefold()
            token_start = start + token_match.start()
            token_end = start + token_match.end()
            chunks.append(emitted)
            traces.append(
                SpanTrace(
                    text=source,
                    start=token_start,
                    end=token_end,
                    policy_class=PolicyClass.CONTEXT_NORMALISABLE,
                    action=(
                        SpanAction.PRESERVED
                        if emitted == source
                        else SpanAction.TRANSFORMED
                    ),
                    emitted_text=emitted,
                    normalised_surface=source.casefold(),
                    candidate_lemma=candidate,
                )
            )
            cursor = token_match.end()

        chunks.append(unicodedata.normalize("NFC", segment[cursor:]))
        return "".join(chunks), traces

    @staticmethod
    def _apply_policy(source: str, rule: PolicyRule) -> tuple[str, SpanAction]:
        if rule.policy_class is PolicyClass.EXACT_PRESERVE:
            return source, SpanAction.PRESERVED
        if rule.policy_class is PolicyClass.APPROVED_ALIAS:
            assert rule.approved_output is not None
            return rule.approved_output, SpanAction.TRANSFORMED
        if rule.policy_class is PolicyClass.REVIEW_OR_ABSTAIN:
            return source, SpanAction.REVIEW
        candidate = source.casefold()
        return candidate, (
            SpanAction.PRESERVED if candidate == source else SpanAction.TRANSFORMED
        )
