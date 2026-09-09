from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import FollowUpResponseValue
from app.domain.types import uuid7

from .shared import Base, state_constraint, state_enum, tenant_identity_constraint


class FollowUpRequest(Base):
    __tablename__ = "follow_up_request"
    __table_args__ = (
        tenant_identity_constraint("follow_up_request"),
        UniqueConstraint(
            "organization_id",
            "navigation_task_id",
            name="uq_follow_up_request_organization_navigation_task",
        ),
        ForeignKeyConstraint(
            ["organization_id", "patient_id", "care_episode_id", "reported_need_id"],
            [
                "reported_need.organization_id",
                "reported_need.patient_id",
                "reported_need.care_episode_id",
                "reported_need.id",
            ],
            name="fk_follow_up_request_reported_need",
        ),
        ForeignKeyConstraint(
            ["organization_id", "patient_id", "reported_need_id", "navigation_task_id"],
            [
                "navigation_task.organization_id",
                "navigation_task.patient_id",
                "navigation_task.reported_need_id",
                "navigation_task.id",
            ],
            name="fk_follow_up_request_navigation_task",
        ),
        CheckConstraint("prompt_version = 1", name="ck_follow_up_request_prompt_version"),
        Index(
            "ix_follow_up_request_org_patient_requested",
            "organization_id",
            "patient_id",
            "requested_at",
            "id",
        ),
        Index(
            "ix_follow_up_request_org_need_requested",
            "organization_id",
            "reported_need_id",
            "requested_at",
            "id",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    care_episode_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    reported_need_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    navigation_task_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", name="fk_follow_up_request_requested_by_user"),
        nullable=False,
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class FollowUpResponse(Base):
    __tablename__ = "follow_up_response"
    __table_args__ = (
        tenant_identity_constraint("follow_up_response"),
        state_constraint("follow_up_response", "response", FollowUpResponseValue),
        UniqueConstraint(
            "organization_id",
            "follow_up_request_id",
            name="uq_follow_up_response_organization_request",
        ),
        ForeignKeyConstraint(
            ["organization_id", "follow_up_request_id"],
            ["follow_up_request.organization_id", "follow_up_request.id"],
            name="fk_follow_up_response_request",
        ),
        ForeignKeyConstraint(
            ["organization_id", "submitted_by_user_id", "patient_identity_link_id"],
            [
                "patient_identity_link.organization_id",
                "patient_identity_link.user_id",
                "patient_identity_link.id",
            ],
            name="fk_follow_up_response_patient_identity_link",
        ),
        CheckConstraint(
            "note IS NULL OR char_length(note) <= 2000",
            name="ck_follow_up_response_note_length",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    follow_up_request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    submitted_by_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    patient_identity_link_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    response: Mapped[FollowUpResponseValue] = mapped_column(
        state_enum(FollowUpResponseValue, "follow_up_response_value"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
