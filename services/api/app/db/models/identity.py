from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.domain.enums import UserRole
from app.domain.types import uuid7

from .shared import Base, state_constraint, state_enum, tenant_identity_constraint


class Organization(Base):
    __tablename__ = "organization"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "user_account"
    __table_args__ = (
        Index("ix_user_account_email", "email", unique=True),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    primary_organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organization.id", name="fk_user_account_primary_organization")
    )
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RoleAssignment(Base):
    __tablename__ = "role_assignment"
    __table_args__ = (
        tenant_identity_constraint("role_assignment"),
        UniqueConstraint(
            "organization_id",
            "user_id",
            "id",
            name="uq_role_assignment_organization_user_id",
        ),
        state_constraint("role_assignment", "role", UserRole),
        CheckConstraint(
            "revoked_at IS NULL OR granted_at <= revoked_at",
            name="ck_role_assignment_grant_interval",
        ),
        Index(
            "ix_role_assignment_active_org_user_role",
            "organization_id",
            "user_id",
            "role",
            unique=True,
            postgresql_where="revoked_at IS NULL",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", name="fk_role_assignment_user_account"), nullable=False
    )
    role: Mapped[UserRole] = mapped_column(state_enum(UserRole, "user_role"), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PatientIdentityLink(Base):
    __tablename__ = "patient_identity_link"
    __table_args__ = (
        tenant_identity_constraint("patient_identity_link"),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_patient_identity_link_organization_patient",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR linked_at <= revoked_at",
            name="ck_patient_identity_link_interval",
        ),
        Index(
            "ix_patient_identity_link_active_user",
            "organization_id",
            "user_id",
            unique=True,
            postgresql_where="revoked_at IS NULL",
        ),
        Index(
            "ix_patient_identity_link_active_patient",
            "organization_id",
            "patient_id",
            unique=True,
            postgresql_where="revoked_at IS NULL",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyntheticPatient(Base):
    __tablename__ = "synthetic_patient"
    __table_args__ = (
        tenant_identity_constraint("synthetic_patient"),
        Index(
            "ix_synthetic_patient_org_external_ref", "organization_id", "external_ref", unique=True
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    external_ref: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(255))
    birth_date: Mapped[date | None] = mapped_column(Date)
    demographics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
