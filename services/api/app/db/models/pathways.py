from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.domain.enums import CareEpisodeStatus
from app.domain.types import uuid7

from .shared import Base, state_constraint, state_enum, tenant_identity_constraint


class PathwayDefinition(Base):
    __tablename__ = "pathway_definition"
    __table_args__ = (
        tenant_identity_constraint("pathway_definition"),
        Index(
            "ix_pathway_definition_org_slug_version",
            "organization_id",
            "slug",
            "version",
            unique=True,
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CareEpisode(Base):
    __tablename__ = "care_episode"
    __table_args__ = (
        tenant_identity_constraint("care_episode"),
        state_constraint("care_episode", "status", CareEpisodeStatus),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_care_episode_organization_patient",
        ),
        Index(
            "ix_care_episode_org_patient_id",
            "organization_id",
            "patient_id",
            "id",
            unique=True,
        ),
        Index("ix_care_episode_org_patient_status", "organization_id", "patient_id", "status"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[CareEpisodeStatus] = mapped_column(
        state_enum(CareEpisodeStatus, "care_episode_status"), default=CareEpisodeStatus.ACTIVE
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EpisodePathwayAssignment(Base):
    __tablename__ = "episode_pathway_assignment"
    __table_args__ = (
        tenant_identity_constraint("episode_pathway_assignment"),
        CheckConstraint(
            "effective_to IS NULL OR effective_from < effective_to",
            name="ck_episode_pathway_assignment_effective_interval",
        ),
        ForeignKeyConstraint(
            ["organization_id", "care_episode_id"],
            ["care_episode.organization_id", "care_episode.id"],
            name="fk_episode_pathway_assignment_organization_episode",
        ),
        ForeignKeyConstraint(
            ["organization_id", "pathway_definition_id"],
            ["pathway_definition.organization_id", "pathway_definition.id"],
            name="fk_episode_pathway_assignment_organization_pathway",
        ),
        ExcludeConstraint(
            ("organization_id", "="),
            ("care_episode_id", "="),
            (text("tstzrange(effective_from, effective_to, '[)')"), "&&"),
            name="ex_episode_pathway_assignment_no_overlap",
            using="gist",
        ),
        Index(
            "ix_episode_pathway_assignment_org_episode_effective_from",
            "organization_id",
            "care_episode_id",
            "effective_from",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    care_episode_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    pathway_definition_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    migration_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    authored_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CheckInDefinition(Base):
    __tablename__ = "check_in_definition"
    __table_args__ = (
        tenant_identity_constraint("check_in_definition"),
        ForeignKeyConstraint(
            ["organization_id", "pathway_definition_id"],
            ["pathway_definition.organization_id", "pathway_definition.id"],
            name="fk_check_in_definition_organization_pathway_definition",
        ),
        Index(
            "ix_check_in_definition_org_pathway_slug_version",
            "organization_id",
            "pathway_definition_id",
            "slug",
            "version",
            unique=True,
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    pathway_definition_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    slug: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    questionnaire: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    pathway_definition: Mapped[PathwayDefinition] = relationship()
