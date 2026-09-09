from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

PUBLIC_DEMO_PHI_WARNING = (
    "This public synthetic demo must not receive real health information or contact details. "
    "Please remove email addresses, phone numbers, and medical-record identifiers."
)

_EMAIL_PATTERN = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_PHONE_PATTERN = re.compile(r"(?:\+?\d[\d(). -]{7,}\d)")
_MRN_PATTERN = re.compile(
    r"\b(?:mrn|medical[ -]?record(?:[ -]?(?:number|no))?)\s*[:#-]?\s*[A-Za-z0-9-]{4,}\b",
    re.IGNORECASE,
)
_UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_ISO_TIMESTAMP_PATTERN = re.compile(
    r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\b",
    re.IGNORECASE,
)
_CONTACT_FIELDS = {
    "contact",
    "contact_email",
    "email",
    "mobile",
    "phone",
    "phone_number",
    "telephone",
}
_NON_CONTENT_FIELDS = {
    "id",
    "idempotency_key",
    "link_id",
    "organization_id",
    "patient_id",
    "qualifying_role_assignment_id",
    "questionnaire_version",
    "role_assignment_id",
    "supersedes_submission_id",
    "timestamp",
}


def contains_real_phi(value: Any, key: str | None = None) -> bool:
    """Conservative public-demo guardrail for prose, not a clinical classifier."""
    normalized_key = key.lower() if key is not None else None
    if normalized_key in _NON_CONTENT_FIELDS:
        return False
    if normalized_key in _CONTACT_FIELDS:
        return True
    if isinstance(value, Mapping):
        return any(contains_real_phi(item, str(item_key)) for item_key, item in value.items())
    if isinstance(value, list):
        return any(contains_real_phi(item) for item in value)
    if not isinstance(value, str):
        return False
    prose = _ISO_TIMESTAMP_PATTERN.sub("", _UUID_PATTERN.sub("", value))
    return bool(
        _EMAIL_PATTERN.search(prose)
        or _PHONE_PATTERN.search(prose)
        or _MRN_PATTERN.search(prose)
    )


def validate_public_demo_text(value: str | None) -> None:
    if value is not None and contains_real_phi(value):
        raise ValueError(PUBLIC_DEMO_PHI_WARNING)
