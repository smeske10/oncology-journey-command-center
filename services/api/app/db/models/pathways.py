from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
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
        ForeignKeyConstraint(
            ["organization_id", "pathway_definition_id"],
            ["pathway_definition.organization_id", "pathway_definition.id"],
            name="fk_care_episode_organization_pathway_definition",
        ),
        Index("ix_care_episode_org_patient_status", "organization_id", "patient_id", "status"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    pathway_definition_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[CareEpisodeStatus] = mapped_column(
        state_enum(CareEpisodeStatus, "care_episode_status"), default=CareEpisodeStatus.ACTIVE
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
