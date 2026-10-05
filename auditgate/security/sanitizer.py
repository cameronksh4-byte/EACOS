"""PII / financial-identifier sanitization with reversible placeholders.

Before any text leaves the machine, sensitive values are swapped for opaque tokens
like ``[[EMAIL_1]]``. The originals stay in a local :class:`Vault`. After the model
answers, :meth:`Vault.rehydrate` puts the real values back into the structured result.

    raw  ->  sanitize()  ->  "[[PERSON_1]] owes $40 (acct [[BANK_ACCOUNT_1]])"  ->  LLM
    LLM result  ->  vault.rehydrate()  ->  real names/numbers, only on this machine

Detection layers (cheapest and most precise first):
  1. Deterministic regexes with validation (Luhn for cards, structure for SSN/EIN/IBAN).
  2. Label-anchored fields ("Account #: 12345678", "Attn: Jane Doe").
  3. A caller-supplied deny-list (client names, project code names).
  4. Optional spaCy NER for free-text person names (and orgs, if enabled).

Regexes give precision; NER gives recall. Neither is perfect, which is why
``evals/runner.py`` measures leakage on every change.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

log = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"\[\[([A-Z_]+)_(\d+)\]\]")
_LINE_LABEL = re.compile(r"^[ \t]*[A-Za-z][\w ()#/.&-]{0,40}:[ \t]*")


def luhn_valid(number: str) -> bool:
    digits = [int(d) for d in re.sub(r"\D", "", number)]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def _ssn_valid(value: str) -> bool:
    area, group, serial = value.split("-")
    return area not in ("000", "666") and not area.startswith("9") and group != "00" and serial != "0000"


@dataclass(frozen=True)
class Detector:
    label: str
    pattern: re.Pattern[str]
    group: int = 0  # which capture group holds the sensitive value
    validate: Callable[[str], bool] | None = None


_LABEL_VALUE = r"[:#\s]*(?:No\.?|Number|#)?[:#\s]*"

DETECTORS: list[Detector] = [
    Detector("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    Detector("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b")),
    Detector("CREDIT_CARD", re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), validate=luhn_valid),
    Detector("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), validate=_ssn_valid),
    Detector("EIN", re.compile(r"\b\d{2}-\d{7}\b")),
    Detector("PHONE", re.compile(
        r"(?<![\w-])(?:\+?1[ .-]?)?(?:\(\d{3}\)\s?|\d{3}[ .-])\d{3}[ .-]\d{4}\b")),
    Detector("IP_ADDRESS", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
    Detector("ADDRESS", re.compile(
        r"\b\d{1,6}[ \t]+(?:[A-Z][a-z0-9]+[ \t]+){1,4}"
        r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|Ct|Court|Pkwy|Hwy)\b\.?"
        r"(?:,?[ \t]+(?:Suite|Ste|Unit|Apt)\.?[ \t]*\w+)?")),
    # Label-anchored identifiers: only the captured value is redacted, the label stays.
    Detector("ROUTING_NUMBER", re.compile(
        r"(?i)\b(?:routing|ABA|RTN)" + _LABEL_VALUE + r"(\d{9})\b"), group=1),
    Detector("BANK_ACCOUNT", re.compile(
        r"(?i)\b(?:account|acct)" + _LABEL_VALUE + r"(\d{6,17})\b"), group=1),
    Detector("PERSON", re.compile(
        r"(?m)\b(?:Attn|Attention|Contact|Signed by|Approved by|Prepared by|Customer Name|Requested by|Technician)"
        r"[ \t]*:[ \t]*((?:(?:Mr|Ms|Mrs|Dr)\.[ \t]?)?[A-Z][a-z'-]+(?:[ \t]+[A-Z][a-z'.-]*){0,3})"), group=1),
]


@lru_cache(maxsize=4)
def _load_spacy(model: str) -> Any | None:
    try:
        import spacy

        return spacy.load(model)
    except Exception as exc:  # model not downloaded, or spaCy unavailable
        log.info("spaCy model %r unavailable (%s); using regex/label detection only.", model, exc)
        return None


@dataclass
class Vault:
    """Bidirectional map between placeholders and original values. Never send this offsite."""

    token_to_value: dict[str, str] = field(default_factory=dict)
    value_to_token: dict[str, str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)

    def token_for(self, label: str, value: str) -> str:
        if value in self.value_to_token:
            return self.value_to_token[value]
        self.counters[label] = self.counters.get(label, 0) + 1
        token = f"[[{label}_{self.counters[label]}]]"
        self.token_to_value[token] = value
        self.value_to_token[value] = token
        return token

    def rehydrate(self, obj: Any) -> Any:
        """Recursively restore originals in strings, lists, tuples and dicts."""
        if isinstance(obj, str):
            return TOKEN_RE.sub(lambda m: self.token_to_value.get(m.group(0), m.group(0)), obj)
        if isinstance(obj, dict):
            return {k: self.rehydrate(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return type(obj)(self.rehydrate(v) for v in obj)
        return obj

    @property
    def counts(self) -> dict[str, int]:
        return dict(self.counters)


@dataclass
class SanitizedText:
    text: str
    vault: Vault

    def leaked(self, sensitive_values: Iterable[str]) -> list[str]:
        """Return any of the given values still present in the sanitized text."""
        return [v for v in sensitive_values if v and v in self.text]


class Sanitizer:
    def __init__(
        self,
        deny_terms: Iterable[str] = (),
        spacy_model: str | None = None,
        redact_orgs: bool = False,
        detectors: list[Detector] | None = None,
    ) -> None:
        self.deny_terms = sorted({t for t in deny_terms if t}, key=len, reverse=True)
        self.spacy_model = spacy_model
        self.redact_orgs = redact_orgs
        self.detectors = detectors if detectors is not None else DETECTORS

    @classmethod
    def from_settings(cls, settings: Any) -> Sanitizer:
        return cls(deny_terms=settings.deny_terms, spacy_model=settings.spacy_model,
                   redact_orgs=settings.redact_orgs)

    def _spans(self, text: str) -> list[tuple[int, int, str]]:
        spans: list[tuple[int, int, str]] = []
        for det in self.detectors:
            for m in det.pattern.finditer(text):
                value = m.group(det.group)
                if not value or (det.validate and not det.validate(value)):
                    continue
                spans.append((m.start(det.group), m.end(det.group), det.label))
        for term in self.deny_terms:
            for m in re.finditer(re.escape(term), text, flags=re.IGNORECASE):
                spans.append((m.start(), m.end(), "REDACTED"))
        nlp = _load_spacy(self.spacy_model) if self.spacy_model else None
        if nlp is not None:
            spans.extend(self._ner_spans(nlp, text))
        return spans

    def _ner_spans(self, nlp: Any, text: str) -> list[tuple[int, int, str]]:
        # Small NER models misfire on document layout: entities run across line breaks
        # and labels get tagged ("Bill" in "Bill To:"). So run NER line by line, on the
        # value part of "Label: value" lines only.
        wanted = {"PERSON"} | ({"ORG"} if self.redact_orgs else set())
        segments: list[tuple[int, str]] = []
        offset = 0
        for line in text.splitlines(keepends=True):
            label = _LINE_LABEL.match(line)
            start = label.end() if label else 0
            if line[start:].strip():
                segments.append((offset + start, line[start:].rstrip("\r\n")))
            offset += len(line)
        spans = []
        for (seg_start, _), doc in zip(segments, nlp.pipe(s for _, s in segments)):
            for ent in doc.ents:
                if ent.label_ in wanted and not TOKEN_RE.search(ent.text):
                    spans.append((seg_start + ent.start_char, seg_start + ent.end_char, ent.label_))
        return spans

    @staticmethod
    def _resolve_overlaps(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
        # Earliest start wins; on ties, the longest span wins.
        chosen: list[tuple[int, int, str]] = []
        for start, end, label in sorted(spans, key=lambda s: (s[0], -(s[1] - s[0]))):
            if chosen and start < chosen[-1][1]:
                continue
            chosen.append((start, end, label))
        return chosen

    def sanitize(self, text: str, vault: Vault | None = None) -> SanitizedText:
        vault = vault or Vault()
        out: list[str] = []
        cursor = 0
        for start, end, label in self._resolve_overlaps(self._spans(text)):
            out.append(text[cursor:start])
            out.append(vault.token_for(label, text[start:end]))
            cursor = end
        out.append(text[cursor:])
        return SanitizedText(text="".join(out), vault=vault)
