from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
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
        tenant_identity_constraint("user_account"),
        Index("ix_user_account_org_email", "organization_id", "email", unique=True),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RoleAssignment(Base):
    __tablename__ = "role_assignment"
    __table_args__ = (
        tenant_identity_constraint("role_assignment"),
        state_constraint("role", UserRole),
        ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["user_account.organization_id", "user_account.id"],
            name="fk_role_assignment_organization_user_account",
        ),
        Index("ix_role_assignment_org_user", "organization_id", "user_id", "role", unique=True),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    role: Mapped[UserRole] = mapped_column(state_enum(UserRole, "user_role"), nullable=False)
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
