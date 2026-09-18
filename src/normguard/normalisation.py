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
            emitted, action, normalised_surface, candidate_lemma = self._apply_policy(
                source, match.rule
            )
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
                    normalised_surface=normalised_surface,
                    candidate_lemma=candidate_lemma,
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
        safety_priority = {
            PolicyClass.EXACT_PRESERVE: 0,
            PolicyClass.APPROVED_ALIAS: 1,
            PolicyClass.REVIEW_OR_ABSTAIN: 2,
            PolicyClass.CONTEXT_NORMALISABLE: 3,
        }
        candidates.sort(
            key=lambda item: (
                safety_priority[item.rule.policy_class],
                -(item.end - item.start),
                item.start,
                item.rule.rule_id,
            )
        )

        selected: list[_Match] = []
        for candidate in candidates:
            if not any(
                candidate.start < existing.end and existing.start < candidate.end
                for existing in selected
            ):
                selected.append(candidate)
        return tuple(sorted(selected, key=lambda item: item.start))

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

    def _apply_policy(
        self,
        source: str,
        rule: PolicyRule,
    ) -> tuple[str, SpanAction, str | None, str | None]:
        if rule.policy_class is PolicyClass.EXACT_PRESERVE:
            return source, SpanAction.PRESERVED, None, None
        if rule.policy_class is PolicyClass.APPROVED_ALIAS:
            assert rule.approved_output is not None
            return rule.approved_output, SpanAction.TRANSFORMED, None, None
        if rule.policy_class is PolicyClass.REVIEW_OR_ABSTAIN:
            return source, SpanAction.REVIEW, None, None

        normalised_surface = unicodedata.normalize("NFC", source).casefold()
        candidate = self._lemmatise(normalised_surface)
        emitted = unicodedata.normalize("NFC", candidate).casefold()
        action = SpanAction.PRESERVED if emitted == source else SpanAction.TRANSFORMED
        return emitted, action, normalised_surface, candidate
