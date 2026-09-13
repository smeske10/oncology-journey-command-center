from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import aliased
from sqlalchemy.sql import Select

from app.auth.models import CurrentActor, ResolvedAuthority, Role
from app.db.models import Organization, PatientIdentityLink, RoleAssignment, User


class AmbiguousAuthorityError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Demo session authority is ambiguous")


class AuthorityDatabaseUnavailableError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Demo session authority database is unavailable")


def authority_from_row(
    row: Row[Any] | Mapping[str, object] | None,
    *,
    organization_id: UUID,
    user_id: UUID,
    role: Role,
) -> ResolvedAuthority | None:
    if row is None:
        return None
    values = row if isinstance(row, Mapping) else row._mapping
    if (
        values["organization_exists"] is not True
        or values["user_exists"] is not True
        or values["user_is_active"] is not True
    ):
        return None
    role_assignment_ids = cast(list[UUID] | None, values["role_assignment_ids"])
    if not role_assignment_ids:
        return None
    if len(role_assignment_ids) != 1:
        raise AmbiguousAuthorityError()
    patient_id: UUID | None = None
    patient_identity_link_id: UUID | None = None
    if role == Role.SUPPORTING_ACTOR:
        patient_identity_link_ids = cast(
            list[UUID] | None, values["patient_identity_link_ids"]
        )
        patient_ids = cast(list[UUID] | None, values["patient_ids"])
        if not patient_identity_link_ids or not patient_ids:
            return None
        if len(patient_identity_link_ids) != 1 or len(patient_ids) != 1:
            raise AmbiguousAuthorityError()
        patient_identity_link_id = patient_identity_link_ids[0]
        patient_id = patient_ids[0]
        reverse_link_ids = cast(
            list[UUID] | None, values["reverse_patient_identity_link_ids"]
        )
        if reverse_link_ids != [patient_identity_link_id]:
            raise AmbiguousAuthorityError()
    return ResolvedAuthority(
        actor=CurrentActor(
            user_id=user_id,
            organization_id=organization_id,
            role=role,
            patient_id=patient_id,
        ),
        role_assignment_id=role_assignment_ids[0],
        patient_identity_link_id=patient_identity_link_id,
    )


def build_authority_statement(
    *,
    organization_id: UUID,
    user_id: UUID,
    role: Role,
    at: datetime,
) -> Select[
    tuple[
        bool,
        bool,
        bool | None,
        list[UUID] | None,
        list[UUID] | None,
        list[UUID] | None,
        list[UUID] | None,
    ]
]:
    effective_role_ids = (
        select(func.array_agg(RoleAssignment.id))
        .where(
            RoleAssignment.organization_id == organization_id,
            RoleAssignment.user_id == user_id,
            RoleAssignment.role == role,
            RoleAssignment.granted_at <= at,
            or_(RoleAssignment.revoked_at.is_(None), at < RoleAssignment.revoked_at),
        )
        .scalar_subquery()
    )
    user_is_active = (
        select(User.is_active).where(User.id == user_id).scalar_subquery()
    )
    effective_link_predicates = (
        PatientIdentityLink.organization_id == organization_id,
        PatientIdentityLink.user_id == user_id,
        PatientIdentityLink.linked_at <= at,
        or_(PatientIdentityLink.revoked_at.is_(None), at < PatientIdentityLink.revoked_at),
    )
    effective_link_ids = (
        select(func.array_agg(PatientIdentityLink.id))
        .where(*effective_link_predicates)
        .scalar_subquery()
    )
    effective_patient_ids = (
        select(func.array_agg(PatientIdentityLink.patient_id))
        .where(*effective_link_predicates)
        .scalar_subquery()
    )
    reverse_link = aliased(PatientIdentityLink)
    effective_forward_patient_ids = select(PatientIdentityLink.patient_id).where(
        *effective_link_predicates
    )
    effective_reverse_link_ids = (
        select(func.array_agg(reverse_link.id))
        .where(
            reverse_link.organization_id == organization_id,
            reverse_link.patient_id.in_(effective_forward_patient_ids),
            reverse_link.linked_at <= at,
            or_(reverse_link.revoked_at.is_(None), at < reverse_link.revoked_at),
        )
        .scalar_subquery()
    )
    return select(
        exists().where(Organization.id == organization_id).label("organization_exists"),
        exists().where(User.id == user_id).label("user_exists"),
        user_is_active.label("user_is_active"),
        effective_role_ids.label("role_assignment_ids"),
        effective_link_ids.label("patient_identity_link_ids"),
        effective_patient_ids.label("patient_ids"),
        effective_reverse_link_ids.label("reverse_patient_identity_link_ids"),
    )
