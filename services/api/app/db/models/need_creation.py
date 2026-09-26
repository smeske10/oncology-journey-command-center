"""Static metadata for the database-guarded need creation command records."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.naming import conv

from .shared import Base

need_creation_policy = sa.Table(
    "need_creation_policy",
    Base.metadata,
    sa.Column("policy_id", sa.Text(), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("required_approver_role", sa.Text(), nullable=False),
    sa.Column("required_approval_count", sa.Integer(), nullable=False),
    sa.Column("allow_self_approval", sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint("policy_id", "version", name=conv("need_creation_policy_pkey")),
    sa.CheckConstraint(
        "allow_self_approval", name=conv("need_creation_policy_allow_self_approval_check")
    ),
    sa.CheckConstraint(
        "required_approval_count = 1",
        name=conv("need_creation_policy_required_approval_count_check"),
    ),
    sa.CheckConstraint(
        "required_approver_role = 'navigator'::text",
        name=conv("need_creation_policy_required_approver_role_check"),
    ),
)

need_creation_proposal = sa.Table(
    "need_creation_proposal",
    Base.metadata,
    sa.Column("id", sa.Uuid(), nullable=False),
    sa.Column("organization_id", sa.Uuid(), nullable=False),
    sa.Column("chain_root_id", sa.Uuid(), nullable=False),
    sa.Column("source_submission_id", sa.Uuid(), nullable=False),
    sa.Column("patient_id", sa.Uuid(), nullable=False),
    sa.Column("care_episode_id", sa.Uuid(), nullable=False),
    sa.Column("proposed_by_user_id", sa.Uuid(), nullable=False),
    sa.Column("proposer_role_assignment_id", sa.Uuid(), nullable=False),
    sa.Column("rationale", sa.Text(), nullable=False),
    sa.Column("evidence", JSONB(), nullable=False),
    sa.Column("canonical_evidence", sa.Text(), nullable=False),
    sa.Column("evidence_sha256", sa.Text(), nullable=False),
    sa.Column("policy_id", sa.Text(), nullable=False),
    sa.Column("policy_version", sa.Integer(), nullable=False),
    sa.Column("policy_snapshot", JSONB(), nullable=False),
    sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint("id", name=conv("need_creation_proposal_pkey")),
    sa.UniqueConstraint(
        "organization_id", "id", name=conv("need_creation_proposal_organization_id_id_key")
    ),
    sa.ForeignKeyConstraint(
        ["organization_id"],
        ["organization.id"],
        name=conv("need_creation_proposal_organization_id_fkey"),
    ),
    sa.ForeignKeyConstraint(
        ["organization_id", "patient_id", "care_episode_id", "chain_root_id"],
        [
            "check_in_submission.organization_id",
            "check_in_submission.patient_id",
            "check_in_submission.care_episode_id",
            "check_in_submission.id",
        ],
        name=conv("need_creation_proposal_organization_id_patient_id_care_ep_fkey1"),
    ),
    sa.ForeignKeyConstraint(
        ["organization_id", "patient_id", "care_episode_id", "source_submission_id"],
        [
            "check_in_submission.organization_id",
            "check_in_submission.patient_id",
            "check_in_submission.care_episode_id",
            "check_in_submission.id",
        ],
        name=conv("need_creation_proposal_organization_id_patient_id_care_epi_fkey"),
    ),
    sa.ForeignKeyConstraint(
        ["organization_id", "proposed_by_user_id", "proposer_role_assignment_id"],
        ["role_assignment.organization_id", "role_assignment.user_id", "role_assignment.id"],
        name=conv("need_creation_proposal_organization_id_proposed_by_user_id_fkey"),
    ),
    sa.ForeignKeyConstraint(
        ["policy_id", "policy_version"],
        ["need_creation_policy.policy_id", "need_creation_policy.version"],
        name=conv("need_creation_proposal_policy_id_policy_version_fkey"),
    ),
    sa.ForeignKeyConstraint(
        ["proposed_by_user_id"],
        ["user_account.id"],
        name=conv("need_creation_proposal_proposed_by_user_id_fkey"),
    ),
    sa.CheckConstraint(
        "length(evidence_sha256) = 64", name=conv("need_creation_proposal_evidence_sha256_check")
    ),
    sa.CheckConstraint(
        "length(btrim(rationale)) >= 1 AND length(btrim(rationale)) <= 4000",
        name=conv("need_creation_proposal_rationale_check"),
    ),
    sa.Index(
        "ix_need_creation_proposal_org_chain", "organization_id", "chain_root_id", unique=False
    ),
)

need_creation_decision = sa.Table(
    "need_creation_decision",
    Base.metadata,
    sa.Column("id", sa.Uuid(), nullable=False),
    sa.Column("organization_id", sa.Uuid(), nullable=False),
    sa.Column("proposal_id", sa.Uuid(), nullable=False),
    sa.Column("authorized_by_user_id", sa.Uuid(), nullable=False),
    sa.Column("qualifying_role_assignment_id", sa.Uuid(), nullable=False),
    sa.Column("qualifying_role_snapshot", sa.Text(), nullable=False),
    sa.Column("decision", sa.Text(), nullable=False),
    sa.Column("reason", sa.Text(), nullable=True),
    sa.Column("outcome", sa.Text(), nullable=False),
    sa.Column("chain_root_id", sa.Uuid(), nullable=False),
    sa.Column("reported_need_id", sa.Uuid(), nullable=True),
    sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint("id", name=conv("need_creation_decision_pkey")),
    sa.UniqueConstraint(
        "organization_id", "id", name=conv("need_creation_decision_organization_id_id_key")
    ),
    sa.ForeignKeyConstraint(
        ["authorized_by_user_id"],
        ["user_account.id"],
        name=conv("need_creation_decision_authorized_by_user_id_fkey"),
    ),
    sa.ForeignKeyConstraint(
        ["organization_id", "authorized_by_user_id", "qualifying_role_assignment_id"],
        ["role_assignment.organization_id", "role_assignment.user_id", "role_assignment.id"],
        name=conv("need_creation_decision_organization_id_authorized_by_user__fkey"),
    ),
    sa.ForeignKeyConstraint(
        ["organization_id", "chain_root_id"],
        ["check_in_submission.organization_id", "check_in_submission.id"],
        name=conv("need_creation_decision_organization_id_chain_root_id_fkey"),
    ),
    sa.ForeignKeyConstraint(
        ["organization_id"],
        ["organization.id"],
        name=conv("need_creation_decision_organization_id_fkey"),
    ),
    sa.ForeignKeyConstraint(
        ["organization_id", "proposal_id"],
        ["need_creation_proposal.organization_id", "need_creation_proposal.id"],
        name=conv("need_creation_decision_organization_id_proposal_id_fkey"),
    ),
    sa.ForeignKeyConstraint(
        ["organization_id", "reported_need_id"],
        ["reported_need.organization_id", "reported_need.id"],
        name=conv("need_creation_decision_organization_id_reported_need_id_fkey"),
    ),
    sa.CheckConstraint(
        "(outcome = 'created'::text) = (reported_need_id IS NOT NULL)",
        name=conv("need_creation_decision_check"),
    ),
    sa.CheckConstraint(
        "decision <> 'declined'::text OR COALESCE(length(btrim(reason)), 0) > 0",
        name=conv("need_creation_decision_check1"),
    ),
    sa.CheckConstraint(
        "decision = ANY (ARRAY['approved'::text, 'declined'::text])",
        name=conv("need_creation_decision_decision_check"),
    ),
    sa.CheckConstraint(
        "outcome = ANY (ARRAY['created'::text, 'declined'::text, 'stale_evidence'::text, "
        "'already_created'::text, 'proposal_decided'::text])",
        name=conv("need_creation_decision_outcome_check"),
    ),
    sa.CheckConstraint(
        "qualifying_role_snapshot = 'navigator'::text",
        name=conv("need_creation_decision_qualifying_role_snapshot_check"),
    ),
    sa.CheckConstraint("length(reason) <= 4000", name=conv("need_creation_decision_reason_check")),
    sa.Index(
        "ix_need_creation_decision_org_chain_created",
        "organization_id",
        "chain_root_id",
        unique=True,
        postgresql_where=sa.text("(outcome = 'created'::text)"),
    ),
    sa.Index(
        "ix_need_creation_decision_org_proposal", "organization_id", "proposal_id", unique=False
    ),
)
