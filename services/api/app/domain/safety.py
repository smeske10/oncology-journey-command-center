"""Safety-signal commands; PostgreSQL owns terminal exclusion."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.db.models import SafetySignal, SafetySignalResolution
from app.domain.enums import SafetySeverity, SafetySignalStatus

logger = logging.getLogger(__name__)

_RESOLUTION_CONFLICT_TRIGGER_DIAGNOSTICS = frozenset(
    {
        "Safety signal is outside the resolution organization",
        "Safety signal must be acknowledged before resolution",
        "Dismissed safety signal cannot be resolved",
        "Resolution reason is required",
    }
)
_DUPLICATE_RESOLUTION_CONSTRAINT = "uq_safety_signal_resolution_safety_signal_id"
_DUPLICATE_RESOLUTION_DIAGNOSTIC = "Safety signal is already resolved"


class SafetySignalNotFound(LookupError):
    """The signal is outside the selected organization or absent."""


class SafetySignalConflict(ValueError):
    """The requested command conflicts with current signal state."""


@dataclass(frozen=True)
class AcknowledgementResult:
    signal_id: UUID
    acknowledged_by_user_id: UUID
    acknowledged_at: datetime
    effective_state: str


@dataclass(frozen=True)
class ResolutionResult:
    resolution_id: UUID
    signal_id: UUID
    resolved_by_user_id: UUID
    resolved_at: datetime
    resolution_reason: str
    effective_state: str


_SEVERITY_RANK = {
    SafetySeverity.ROUTINE: 0,
    SafetySeverity.URGENT: 1,
    SafetySeverity.EMERGENT: 2,
}


def severity_rank(value: str | SafetySeverity) -> int:
    return _SEVERITY_RANK[SafetySeverity(value)]


def validate_automated_severity(
    deterministic_level: str | SafetySeverity,
    effective_level: str | SafetySeverity,
) -> str:
    deterministic = SafetySeverity(deterministic_level)
    effective = SafetySeverity(effective_level)
    if severity_rank(effective) < severity_rank(deterministic):
        raise ValueError("Automation cannot lower severity below the deterministic level")
    return effective.value


def acknowledge_signal(
    session: Session,
    *,
    organization_id: UUID,
    signal_id: UUID,
    acknowledged_by_user_id: UUID,
) -> AcknowledgementResult:
    signal = _locked_signal(session, organization_id, signal_id)
    if signal.acknowledged_at is not None:
        raise SafetySignalConflict("Safety signal is already acknowledged")
    if _effective_state(session, organization_id, signal_id) in {"resolved", "dismissed"}:
        raise SafetySignalConflict("Terminal safety signal cannot be acknowledged again")
    acknowledged_at = datetime.now(UTC)
    signal.status = SafetySignalStatus.ACKNOWLEDGED
    signal.acknowledged_by_user_id = acknowledged_by_user_id
    signal.acknowledged_at = acknowledged_at
    session.flush()
    return AcknowledgementResult(
        signal_id=signal.id,
        acknowledged_by_user_id=acknowledged_by_user_id,
        acknowledged_at=acknowledged_at,
        effective_state="acknowledged",
    )


def resolve_signal(
    session: Session,
    *,
    organization_id: UUID,
    signal_id: UUID,
    resolved_by_user_id: UUID,
    resolution_reason: str,
) -> ResolutionResult:
    reason = resolution_reason.strip()
    if not reason:
        raise ValueError("Resolution reason must not be blank")
    signal = _locked_signal(session, organization_id, signal_id)
    if signal.acknowledged_at is None:
        raise SafetySignalConflict("Safety signal must be acknowledged before resolution")
    if signal.dismissal_proposed_change_id is not None:
        raise SafetySignalConflict("Dismissed safety signal cannot be resolved")
    if session.scalar(
        select(SafetySignalResolution.id).where(
            SafetySignalResolution.organization_id == organization_id,
            SafetySignalResolution.safety_signal_id == signal_id,
        )
    ) is not None:
        raise SafetySignalConflict("Safety signal is already resolved")

    resolved_at = datetime.now(UTC)
    resolution = SafetySignalResolution(
        organization_id=organization_id,
        safety_signal_id=signal_id,
        resolved_by_user_id=resolved_by_user_id,
        resolved_at=resolved_at,
        resolution_reason=reason,
    )
    try:
        with session.begin_nested():
            session.add(resolution)
            session.flush()
    except (IntegrityError, DBAPIError) as error:
        conflict = _resolution_domain_error(error)
        if conflict is None:
            logger.exception("Unexpected database error while resolving a safety signal")
            raise
        raise conflict from None
    return ResolutionResult(
        resolution_id=resolution.id,
        signal_id=signal_id,
        resolved_by_user_id=resolved_by_user_id,
        resolved_at=resolved_at,
        resolution_reason=reason,
        effective_state="resolved",
    )


def _locked_signal(session: Session, organization_id: UUID, signal_id: UUID) -> SafetySignal:
    signal = session.scalar(
        select(SafetySignal)
        .where(
            SafetySignal.organization_id == organization_id,
            SafetySignal.id == signal_id,
        )
        .with_for_update()
    )
    if signal is None:
        raise SafetySignalNotFound("Safety signal not found")
    return signal


def _effective_state(session: Session, organization_id: UUID, signal_id: UUID) -> str:
    value = session.scalar(
        text(
            "SELECT effective_state FROM effective_safety_signal_state "
            "WHERE organization_id = :organization_id AND id = :signal_id"
        ),
        {"organization_id": organization_id, "signal_id": signal_id},
    )
    if value is None:
        raise SafetySignalNotFound("Safety signal not found")
    return str(value)


def _database_message(error: DBAPIError) -> str:
    return str(getattr(error, "orig", error)).splitlines()[0]


def _resolution_domain_error(error: DBAPIError) -> SafetySignalConflict | None:
    original = getattr(error, "orig", error)
    sqlstate = getattr(original, "sqlstate", None)
    if sqlstate == "P0001":
        diagnostic = _database_message(error)
        if diagnostic in _RESOLUTION_CONFLICT_TRIGGER_DIAGNOSTICS:
            return SafetySignalConflict(diagnostic)
        return None
    diagnostic = getattr(original, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    if sqlstate == "23505" and constraint_name == _DUPLICATE_RESOLUTION_CONSTRAINT:
        return SafetySignalConflict(_DUPLICATE_RESOLUTION_DIAGNOSTIC)
    return None
