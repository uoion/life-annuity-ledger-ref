"""POPIA PII redaction middleware/sanitizer for outbound AI prompts and output checks.

Strips South African personally identifiable information before any free-text
context leaves the system boundary to external inference endpoints.
"""

import re
from dataclasses import dataclass

SA_ID_PATTERN = re.compile(
    r"(?<!\d)(?:\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])[\s-]?\d{4}[\s-]?\d{3})(?!\d)"
)
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
SA_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+27|0)(?:[\s-]?\d){9}(?!\d)")
BANK_ACCOUNT_PATTERN = re.compile(
    r"(?i)\b(account(?:\s+(?:no|num|number))?\s*[:#-]?\s*)(\d[\d\s-]{6,15}\d)\b"
)
HONORIFIC_NAME_PATTERN = re.compile(
    r"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b"
)


@dataclass(frozen=True, slots=True)
class RedactionResult:
    sanitized_text: str
    redaction_counts: dict[str, int]

    @property
    def total_redactions(self) -> int:
        return sum(self.redaction_counts.values())


def _expand_name_tokens(known_names: list[str]) -> list[str]:
    """Build deduplicated full names and individual name tokens (3+ chars), longest first."""
    candidates: set[str] = set()
    for raw_name in known_names:
        cleaned = raw_name.strip()
        if not cleaned:
            continue
        candidates.add(cleaned)
        for token in re.split(r"[\s,-]+", cleaned):
            if len(token) >= 3:
                candidates.add(token)
    return sorted(candidates, key=len, reverse=True)


def redact_popia_pii(text: str, known_names: list[str] | None = None) -> RedactionResult:
    """Redact SA IDs, emails, phone numbers, bank accounts, and personal names/tokens."""
    counts: dict[str, int] = {}
    sanitized = text

    sanitized, sa_id_hits = SA_ID_PATTERN.subn("[REDACTED_SA_ID]", sanitized)
    if sa_id_hits:
        counts["sa_id_number"] = sa_id_hits

    sanitized, email_hits = EMAIL_PATTERN.subn("[REDACTED_EMAIL]", sanitized)
    if email_hits:
        counts["email"] = email_hits

    sanitized, phone_hits = SA_PHONE_PATTERN.subn("[REDACTED_PHONE]", sanitized)
    if phone_hits:
        counts["sa_phone"] = phone_hits

    sanitized, account_hits = BANK_ACCOUNT_PATTERN.subn(r"\1[REDACTED_ACCOUNT]", sanitized)
    if account_hits:
        counts["bank_account"] = account_hits

    sanitized, honorific_hits = HONORIFIC_NAME_PATTERN.subn("[REDACTED_NAME]", sanitized)
    if honorific_hits:
        counts["honorific_name"] = honorific_hits

    if known_names:
        name_hits = 0
        for candidate in _expand_name_tokens(known_names):
            pattern = re.compile(rf"\b{re.escape(candidate)}\b", flags=re.IGNORECASE)
            sanitized, hits = pattern.subn("[REDACTED_NAME]", sanitized)
            name_hits += hits
        if name_hits:
            counts["known_name"] = name_hits

    return RedactionResult(sanitized_text=sanitized, redaction_counts=counts)


def contains_popia_pii(text: str, known_names: list[str] | None = None) -> bool:
    """Return True if any POPIA-sensitive pattern or known name token appears in text."""
    return redact_popia_pii(text, known_names=known_names).total_redactions > 0
