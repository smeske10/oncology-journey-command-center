"""Reconcile identity, pathway history, and submission provenance."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision = "0002_identity_pathway_submission"
down_revision = "0001_core_domain"
branch_labels = None
depends_on = None


SUBMISSION_SOURCE = postgresql.ENUM(
    "patient",
    "authorized_proxy",
    "clinician",
    "import",
    name="submission_source",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    SUBMISSION_SOURCE.create(bind, checkfirst=False)
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.drop_constraint("fk_role_assignment_organization_user_account", "role_assignment")
    op.drop_constraint("fk_navigation_task_organization_assignee", "navigation_task")
    op.drop_constraint("fk_approval_decision_organization_authorized_user", "approval_decision")
    op.drop_constraint("fk_audit_event_organization_actor", "audit_event")
    op.drop_constraint("fk_user_account_organization_id_organization", "user_account")
    op.drop_index("ix_user_account_org_email", table_name="user_account")
    op.drop_constraint("uq_user_account_organization_id_id", "user_account", type_="unique")
    op.add_column("user_account", sa.Column("primary_organization_id", sa.Uuid()))
    op.execute("UPDATE user_account SET primary_organization_id = organization_id")
    op.create_foreign_key(
        "fk_user_account_primary_organization",
        "user_account",
        "organization",
        ["primary_organization_id"],
        ["id"],
    )
    op.drop_column("user_account", "organization_id")
    op.create_index("ix_user_account_email", "user_account", ["email"], unique=True)
    op.create_foreign_key(
        "fk_role_assignment_user_account", "role_assignment", "user_account", ["user_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_navigation_task_assignee_user",
        "navigation_task",
        "user_account",
        ["assignee_user_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_approval_decision_authorized_user",
        "approval_decision",
        "user_account",
        ["authorized_user_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_audit_event_actor_user", "audit_event", "user_account", ["actor_user_id"], ["id"]
    )

    op.drop_index("ix_role_assignment_org_user", table_name="role_assignment")
    op.drop_constraint("uq_role_assignment_organization_id_id", "role_assignment", type_="unique")
    op.add_column(
        "role_assignment",
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("role_assignment", sa.Column("revoked_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE role_assignment SET granted_at = created_at")
    op.alter_column("role_assignment", "granted_at", nullable=False)
    op.create_check_constraint(
        "ck_role_assignment_grant_interval",
        "role_assignment",
        "revoked_at IS NULL OR granted_at <= revoked_at",
    )
    op.create_index(
        "ix_role_assignment_active_org_user_role",
        "role_assignment",
        ["organization_id", "user_id", "role"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "patient_identity_link",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_patient_identity_link"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_patient_identity_link_organization_id_id"
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR linked_at <= revoked_at",
            name="ck_patient_identity_link_interval",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user_account.id"]),
        sa.ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_patient_identity_link_organization_patient",
        ),
    )
    op.create_index(
        "ix_patient_identity_link_active_user",
        "patient_identity_link",
        ["organization_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_patient_identity_link_active_patient",
        "patient_identity_link",
        ["organization_id", "patient_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    if not context.is_offline_mode() and bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM care_episode)")
    ).scalar():
        raise RuntimeError(
            "Cannot infer pathway-assignment authorship for existing care episodes; reseed synthetic data."
        )
    op.drop_constraint("fk_care_episode_organization_pathway_definition", "care_episode")
    op.drop_column("care_episode", "pathway_definition_id")
    op.create_index(
        "ix_care_episode_org_patient_id",
        "care_episode",
        ["organization_id", "patient_id", "id"],
        unique=True,
    )
    op.create_table(
        "episode_pathway_assignment",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("care_episode_id", sa.Uuid(), nullable=False),
        sa.Column("pathway_definition_id", sa.Uuid(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True)),
        sa.Column("migration_reason", sa.String(500), nullable=False),
        sa.Column("authored_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_episode_pathway_assignment"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_episode_pathway_assignment_organization_id_id"
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_from < effective_to",
            name="ck_episode_pathway_assignment_effective_interval",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["authored_by_user_id"], ["user_account.id"]),
        sa.ForeignKeyConstraint(
            ["organization_id", "care_episode_id"],
            ["care_episode.organization_id", "care_episode.id"],
            name="fk_episode_pathway_assignment_organization_episode",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "pathway_definition_id"],
            ["pathway_definition.organization_id", "pathway_definition.id"],
            name="fk_episode_pathway_assignment_organization_pathway",
        ),
        postgresql.ExcludeConstraint(
            ("organization_id", "="),
            ("care_episode_id", "="),
            (sa.text("tstzrange(effective_from, effective_to, '[)')"), "&&"),
            name="ex_episode_pathway_assignment_no_overlap",
            using="gist",
        ),
    )
    op.create_index(
        "ix_episode_pathway_assignment_org_episode_effective_from",
        "episode_pathway_assignment",
        ["organization_id", "care_episode_id", "effective_from"],
    )

    if not context.is_offline_mode() and bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM check_in_submission)")
    ).scalar():
        raise RuntimeError(
            "Cannot infer care-episode or submission provenance for existing submissions; reseed synthetic data."
        )
    op.drop_constraint("ck_check_in_submission_status_state", "check_in_submission", type_="check")
    op.add_column("check_in_submission", sa.Column("care_episode_id", sa.Uuid(), nullable=True))
    op.add_column(
        "check_in_submission",
        sa.Column("submission_source", SUBMISSION_SOURCE, nullable=True),
    )
    op.add_column("check_in_submission", sa.Column("submitted_by_user_id", sa.Uuid()))
    op.add_column("check_in_submission", sa.Column("external_source", sa.String(255)))
    op.add_column("check_in_submission", sa.Column("external_record_id", sa.String(255)))
    op.add_column("check_in_submission", sa.Column("supersedes_submission_id", sa.Uuid()))
    op.alter_column("check_in_submission", "care_episode_id", nullable=False)
    op.alter_column("check_in_submission", "check_in_definition_id", nullable=False)
    op.alter_column("check_in_submission", "submission_source", nullable=False)
    op.alter_column("check_in_submission", "submitted_at", nullable=False)
    op.create_check_constraint(
        "ck_check_in_submission_status_state",
        "check_in_submission",
        "status IN ('submitted', 'processed')",
    )
    op.create_check_constraint(
        "ck_check_in_submission_provenance",
        "check_in_submission",
        "(submission_source = 'import' AND submitted_by_user_id IS NULL "
        "AND external_source IS NOT NULL AND external_record_id IS NOT NULL) OR "
        "(submission_source <> 'import' AND submitted_by_user_id IS NOT NULL "
        "AND external_source IS NULL AND external_record_id IS NULL)",
    )
    op.create_foreign_key(
        "fk_check_in_submission_organization_patient_episode",
        "check_in_submission",
        "care_episode",
        ["organization_id", "patient_id", "care_episode_id"],
        ["organization_id", "patient_id", "id"],
    )
    op.create_foreign_key(
        "fk_check_in_submission_submitted_by_user",
        "check_in_submission",
        "user_account",
        ["submitted_by_user_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_check_in_submission_organization_supersedes_submission",
        "check_in_submission",
        "check_in_submission",
        ["organization_id", "supersedes_submission_id"],
        ["organization_id", "id"],
    )
    op.create_unique_constraint(
        "uq_check_in_submission_supersedes_submission_id",
        "check_in_submission",
        ["supersedes_submission_id"],
    )
    op.execute(
        """
        CREATE VIEW active_check_in_submission AS
        SELECT submission.*
        FROM check_in_submission AS submission
        WHERE NOT EXISTS (
            SELECT 1
            FROM check_in_submission AS successor
            WHERE successor.supersedes_submission_id = submission.id
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS active_check_in_submission")
    op.drop_table("episode_pathway_assignment")
    op.drop_table("patient_identity_link")
    SUBMISSION_SOURCE.drop(op.get_bind(), checkfirst=False)
