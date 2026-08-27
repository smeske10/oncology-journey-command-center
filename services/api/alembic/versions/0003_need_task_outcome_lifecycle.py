"""Make need closure outcome-driven and task-safe."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision = "0003_need_task_outcome_lifecycle"
down_revision = "0002_identity_pathway_submission"
branch_labels = None
depends_on = None


TASK_CANCELLATION_REASON = postgresql.ENUM(
    "need_closed",
    name="task_cancellation_reason",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    TASK_CANCELLATION_REASON.create(bind, checkfirst=False)

    if not context.is_offline_mode():
        op.execute(
            """
            DO $$
            DECLARE ambiguous_id uuid;
            BEGIN
                SELECT candidate.id INTO ambiguous_id
                FROM (
                    SELECT id FROM reported_need
                    WHERE status IN ('resolved', 'closed') OR resolved_at IS NOT NULL
                    UNION ALL
                    SELECT id FROM outcome
                ) AS candidate
                LIMIT 1;
                IF ambiguous_id IS NOT NULL THEN
                    RAISE EXCEPTION
                        'Cannot migrate legacy terminal need/outcome data % without a provable '
                        'Outcome recorder; reset the synthetic demo database and rerun migrations.',
                        ambiguous_id;
                END IF;

                SELECT id INTO ambiguous_id
                FROM navigation_task
                WHERE status = 'cancelled'
                LIMIT 1;
                IF ambiguous_id IS NOT NULL THEN
                    RAISE EXCEPTION
                        'Cannot migrate legacy cancelled navigation task % without provable '
                        'cancellation actor, timestamp, and reason provenance; reset the synthetic '
                        'demo database and rerun migrations.',
                        ambiguous_id;
                END IF;

                SELECT id INTO ambiguous_id
                FROM navigation_task
                WHERE reported_need_id IS NULL
                LIMIT 1;
                IF ambiguous_id IS NOT NULL THEN
                    RAISE EXCEPTION
                        'Cannot migrate navigation task % without a reported need; reset the '
                        'synthetic demo database and rerun migrations.',
                        ambiguous_id;
                END IF;
            END $$;
            """
        )

    op.create_unique_constraint(
        "uq_check_in_submission_org_patient_episode_id",
        "check_in_submission",
        ["organization_id", "patient_id", "care_episode_id", "id"],
    )

    op.add_column("reported_need", sa.Column("care_episode_id", sa.Uuid()))
    op.add_column("reported_need", sa.Column("reopened_from_need_id", sa.Uuid()))
    if not context.is_offline_mode():
        op.execute(
            """
            UPDATE reported_need AS need
            SET care_episode_id = submission.care_episode_id
            FROM check_in_submission AS submission
            WHERE submission.organization_id = need.organization_id
              AND submission.patient_id = need.patient_id
              AND submission.id = need.source_submission_id
            """
        )
        op.execute(
            """
            DO $$
            DECLARE need_id uuid;
            BEGIN
                SELECT id INTO need_id
                FROM reported_need
                WHERE care_episode_id IS NULL
                LIMIT 1;
                IF need_id IS NOT NULL THEN
                    RAISE EXCEPTION
                        'Cannot migrate reported need %: source submission does not prove one '
                        'tenant-, patient-, and episode-aligned origin; reset the synthetic demo '
                        'database and rerun migrations.',
                        need_id;
                END IF;
            END $$;
            """
        )
    op.alter_column("reported_need", "care_episode_id", nullable=False)
    op.alter_column("reported_need", "source_submission_id", nullable=True)
    op.drop_constraint(
        "fk_reported_need_organization_submission", "reported_need", type_="foreignkey"
    )
    op.drop_constraint("ck_reported_need_status_state", "reported_need", type_="check")
    op.execute("ALTER TABLE reported_need ALTER COLUMN status TYPE text USING status::text")
    op.execute("DROP TYPE need_status")
    op.execute("CREATE TYPE need_status AS ENUM ('open', 'in_progress')")
    op.execute(
        "ALTER TABLE reported_need ALTER COLUMN status TYPE need_status "
        "USING status::need_status"
    )
    op.drop_column("reported_need", "resolved_at")
    op.create_check_constraint(
        "ck_reported_need_status_state",
        "reported_need",
        "status IN ('open', 'in_progress')",
    )
    op.create_check_constraint(
        "ck_reported_need_origin",
        "reported_need",
        "num_nonnulls(source_submission_id, reopened_from_need_id) = 1",
    )
    op.create_unique_constraint(
        "uq_reported_need_org_patient_episode_id",
        "reported_need",
        ["organization_id", "patient_id", "care_episode_id", "id"],
    )
    op.create_unique_constraint(
        "uq_reported_need_org_patient_id",
        "reported_need",
        ["organization_id", "patient_id", "id"],
    )
    op.create_unique_constraint(
        "uq_reported_need_reopened_from_need_id",
        "reported_need",
        ["reopened_from_need_id"],
    )
    op.create_foreign_key(
        "fk_reported_need_origin_submission",
        "reported_need",
        "check_in_submission",
        ["organization_id", "patient_id", "care_episode_id", "source_submission_id"],
        ["organization_id", "patient_id", "care_episode_id", "id"],
    )
    op.create_foreign_key(
        "fk_reported_need_reopened_predecessor",
        "reported_need",
        "reported_need",
        ["organization_id", "patient_id", "care_episode_id", "reopened_from_need_id"],
        ["organization_id", "patient_id", "care_episode_id", "id"],
    )

    op.drop_constraint(
        "fk_navigation_task_organization_reported_need",
        "navigation_task",
        type_="foreignkey",
    )
    op.drop_constraint("ck_navigation_task_status_state", "navigation_task", type_="check")
    op.add_column("navigation_task", sa.Column("cancelled_by_user_id", sa.Uuid()))
    op.add_column("navigation_task", sa.Column("cancelled_at", sa.DateTime(timezone=True)))
    op.add_column(
        "navigation_task",
        sa.Column("cancellation_reason", TASK_CANCELLATION_REASON),
    )
    op.execute("ALTER TABLE navigation_task ALTER COLUMN status TYPE text USING status::text")
    op.execute("DROP TYPE navigation_task_status")
    op.execute(
        "CREATE TYPE navigation_task_status AS ENUM "
        "('open', 'assigned', 'in_progress', 'completed', 'cancelled')"
    )
    op.execute(
        "ALTER TABLE navigation_task ALTER COLUMN status TYPE navigation_task_status "
        "USING status::navigation_task_status"
    )
    op.execute(
        "UPDATE navigation_task SET status = 'assigned' "
        "WHERE status = 'open' AND assignee_user_id IS NOT NULL"
    )
    op.execute(
        """
        UPDATE reported_need AS need
        SET status = 'in_progress'
        WHERE need.status = 'open'
          AND EXISTS (
              SELECT 1
              FROM navigation_task AS task
              WHERE task.organization_id = need.organization_id
                AND task.patient_id = need.patient_id
                AND task.reported_need_id = need.id
                AND task.status IN ('assigned', 'in_progress', 'completed')
          )
        """
    )
    op.alter_column("navigation_task", "reported_need_id", nullable=False)
    op.create_foreign_key(
        "fk_navigation_task_parent_need",
        "navigation_task",
        "reported_need",
        ["organization_id", "patient_id", "reported_need_id"],
        ["organization_id", "patient_id", "id"],
    )
    op.create_foreign_key(
        "fk_navigation_task_cancelled_by_user",
        "navigation_task",
        "user_account",
        ["cancelled_by_user_id"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_navigation_task_status_state",
        "navigation_task",
        "status IN ('open', 'assigned', 'in_progress', 'completed', 'cancelled')",
    )
    op.create_check_constraint(
        "ck_navigation_task_assignment_shape",
        "navigation_task",
        "(status = 'open' AND assignee_user_id IS NULL) OR "
        "(status = 'assigned' AND assignee_user_id IS NOT NULL) OR "
        "status IN ('in_progress', 'completed', 'cancelled')",
    )
    op.create_check_constraint(
        "ck_navigation_task_cancellation_shape",
        "navigation_task",
        "(status = 'cancelled' AND cancelled_by_user_id IS NOT NULL "
        "AND cancelled_at IS NOT NULL AND cancellation_reason IS NOT NULL) OR "
        "(status <> 'cancelled' AND cancelled_by_user_id IS NULL "
        "AND cancelled_at IS NULL AND cancellation_reason IS NULL)",
    )

    op.drop_constraint(
        "fk_outcome_organization_reported_need", "outcome", type_="foreignkey"
    )
    op.drop_constraint("ck_outcome_status_state", "outcome", type_="check")
    op.drop_index("ix_outcome_org_patient_created", table_name="outcome")
    op.execute("ALTER TYPE outcome_status RENAME TO outcome_disposition")
    op.alter_column("outcome", "status", new_column_name="disposition")
    op.alter_column("outcome", "reason", new_column_name="note", nullable=True)
    op.alter_column("outcome", "created_at", new_column_name="recorded_at")
    op.add_column("outcome", sa.Column("recorded_by_user_id", sa.Uuid()))
    op.add_column("outcome", sa.Column("idempotency_key", sa.String(255)))
    op.alter_column("outcome", "recorded_by_user_id", nullable=False)
    op.alter_column("outcome", "idempotency_key", nullable=False)
    op.create_check_constraint(
        "ck_outcome_disposition_state",
        "outcome",
        "disposition IN ('resolved', 'closed_unresolved')",
    )
    op.create_foreign_key(
        "fk_outcome_parent_need",
        "outcome",
        "reported_need",
        ["organization_id", "patient_id", "reported_need_id"],
        ["organization_id", "patient_id", "id"],
    )
    op.create_foreign_key(
        "fk_outcome_recorded_by_user",
        "outcome",
        "user_account",
        ["recorded_by_user_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_outcome_organization_idempotency_key",
        "outcome",
        ["organization_id", "idempotency_key"],
    )
    op.create_index(
        "ix_outcome_org_patient_recorded",
        "outcome",
        ["organization_id", "patient_id", "recorded_at"],
    )

    op.execute(
        """
        CREATE VIEW effective_need_state AS
        SELECT
            need.id,
            need.organization_id,
            need.patient_id,
            need.care_episode_id,
            need.source_submission_id,
            need.reopened_from_need_id,
            need.kind,
            need.status,
            need.evidence,
            need.created_at,
            CASE WHEN outcome.id IS NULL THEN need.status::text ELSE 'closed' END AS effective_state
        FROM reported_need AS need
        LEFT JOIN outcome
          ON outcome.organization_id = need.organization_id
         AND outcome.patient_id = need.patient_id
         AND outcome.reported_need_id = need.id
        """
    )

    op.execute(
        """
        CREATE FUNCTION guard_reported_need_reopening()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE predecessor_id uuid;
        BEGIN
            IF NEW.reopened_from_need_id IS NULL THEN
                RETURN NEW;
            END IF;

            SELECT predecessor.id INTO predecessor_id
            FROM reported_need AS predecessor
            WHERE predecessor.organization_id = NEW.organization_id
              AND predecessor.patient_id = NEW.patient_id
              AND predecessor.care_episode_id = NEW.care_episode_id
              AND predecessor.id = NEW.reopened_from_need_id
            FOR UPDATE;

            IF predecessor_id IS NULL THEN
                RAISE EXCEPTION
                    'Reopened reported need predecessor % is outside the tenant, patient, """
            """or episode',
                    NEW.reopened_from_need_id;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM outcome
                WHERE organization_id = NEW.organization_id
                  AND patient_id = NEW.patient_id
                  AND reported_need_id = predecessor_id
            ) THEN
                RAISE EXCEPTION 'Reported need % is active and cannot be reopened', predecessor_id;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reported_need_reopening_guard
        BEFORE INSERT ON reported_need
        FOR EACH ROW
        WHEN (NEW.reopened_from_need_id IS NOT NULL)
        EXECUTE FUNCTION guard_reported_need_reopening()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_reported_need_identity_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.organization_id IS DISTINCT FROM OLD.organization_id
               OR NEW.patient_id IS DISTINCT FROM OLD.patient_id
               OR NEW.care_episode_id IS DISTINCT FROM OLD.care_episode_id
               OR NEW.source_submission_id IS DISTINCT FROM OLD.source_submission_id
               OR NEW.reopened_from_need_id IS DISTINCT FROM OLD.reopened_from_need_id THEN
                RAISE EXCEPTION 'Reported need identity and origin are immutable after insert';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reported_need_identity_update_guard
        BEFORE UPDATE OF organization_id, patient_id, care_episode_id,
                         source_submission_id, reopened_from_need_id
        ON reported_need
        FOR EACH ROW
        EXECUTE FUNCTION guard_reported_need_identity_update()
        """
    )

    op.execute(
        """
        CREATE FUNCTION guard_navigation_task_lifecycle()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE parent_need_id uuid;
        DECLARE closure outcome%ROWTYPE;
        BEGIN
            SELECT need.id INTO parent_need_id
            FROM reported_need AS need
            WHERE need.organization_id = NEW.organization_id
              AND need.patient_id = NEW.patient_id
              AND need.id = NEW.reported_need_id
            FOR UPDATE;

            IF parent_need_id IS NULL THEN
                RAISE EXCEPTION
                    'Parent reported need % is outside the task tenant or patient',
                    NEW.reported_need_id;
            END IF;

            IF TG_OP = 'UPDATE' AND (
                NEW.organization_id IS DISTINCT FROM OLD.organization_id OR
                NEW.patient_id IS DISTINCT FROM OLD.patient_id OR
                NEW.reported_need_id IS DISTINCT FROM OLD.reported_need_id
            ) THEN
                RAISE EXCEPTION 'Navigation task parent identity is immutable';
            END IF;

            IF TG_OP = 'UPDATE'
               AND OLD.status IN ('completed', 'cancelled')
               AND NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'Navigation task % terminal state is irreversible', OLD.id;
            END IF;

            SELECT outcome.* INTO closure
            FROM outcome
            WHERE outcome.organization_id = NEW.organization_id
              AND outcome.patient_id = NEW.patient_id
              AND outcome.reported_need_id = NEW.reported_need_id;

            IF closure.id IS NOT NULL THEN
                IF TG_OP = 'UPDATE'
                   AND OLD.status IN ('open', 'assigned', 'in_progress')
                   AND NEW.status = 'cancelled'
                   AND NEW.cancellation_reason = 'need_closed'
                   AND NEW.cancelled_by_user_id = closure.recorded_by_user_id
                   AND NEW.cancelled_at = closure.recorded_at THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION 'Reported need % is closed', NEW.reported_need_id;
            END IF;

            IF NEW.status = 'cancelled' AND NEW.cancellation_reason = 'need_closed' THEN
                RAISE EXCEPTION
                    'need_closed cancellation requires an authorizing Outcome for reported need %',
                    NEW.reported_need_id;
            END IF;

            IF NEW.status IN ('assigned', 'in_progress') OR NEW.assignee_user_id IS NOT NULL THEN
                UPDATE reported_need
                SET status = 'in_progress'
                WHERE organization_id = NEW.organization_id
                  AND patient_id = NEW.patient_id
                  AND id = NEW.reported_need_id
                  AND status = 'open';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_navigation_task_lifecycle_guard
        BEFORE INSERT OR UPDATE ON navigation_task
        FOR EACH ROW
        EXECUTE FUNCTION guard_navigation_task_lifecycle()
        """
    )

    op.execute(
        """
        CREATE FUNCTION close_reported_need_from_outcome()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE task_record record;
        DECLARE locked_need_id uuid;
        BEGIN
            SELECT need.id INTO locked_need_id
            FROM reported_need AS need
            WHERE need.organization_id = NEW.organization_id
              AND need.patient_id = NEW.patient_id
              AND need.id = NEW.reported_need_id
            FOR UPDATE;

            IF locked_need_id IS NULL THEN
                RAISE EXCEPTION
                    'Outcome reported need % is outside the tenant or patient',
                    NEW.reported_need_id;
            END IF;

            FOR task_record IN
                SELECT task.id
                FROM navigation_task AS task
                WHERE task.organization_id = NEW.organization_id
                  AND task.patient_id = NEW.patient_id
                  AND task.reported_need_id = NEW.reported_need_id
                  AND task.status IN ('open', 'assigned', 'in_progress')
                ORDER BY task.id
                FOR UPDATE
            LOOP
                UPDATE navigation_task
                SET status = 'cancelled',
                    cancelled_by_user_id = NEW.recorded_by_user_id,
                    cancelled_at = NEW.recorded_at,
                    cancellation_reason = 'need_closed'
                WHERE id = task_record.id;

                INSERT INTO audit_event
                    (id, organization_id, actor_user_id, entity_type, entity_id,
                     event_type, payload, created_at)
                VALUES
                    (md5(NEW.id::text || task_record.id::text || 'task_cancelled_by_closure')::uuid,
                     NEW.organization_id,
                     NEW.recorded_by_user_id,
                     'navigation_task',
                     task_record.id,
                     'task_cancelled_by_closure',
                     jsonb_build_object(
                         'outcome_id', NEW.id::text,
                         'cancellation_reason', 'need_closed'
                     ),
                     NEW.recorded_at);
            END LOOP;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_outcome_closes_reported_need
        AFTER INSERT ON outcome
        FOR EACH ROW
        EXECUTE FUNCTION close_reported_need_from_outcome()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_outcome_closes_reported_need ON outcome")
    op.execute("DROP FUNCTION IF EXISTS close_reported_need_from_outcome()")
    op.execute("DROP TRIGGER IF EXISTS trg_navigation_task_lifecycle_guard ON navigation_task")
    op.execute("DROP FUNCTION IF EXISTS guard_navigation_task_lifecycle()")
    op.execute("DROP TRIGGER IF EXISTS trg_reported_need_identity_update_guard ON reported_need")
    op.execute("DROP FUNCTION IF EXISTS guard_reported_need_identity_update()")
    op.execute("DROP TRIGGER IF EXISTS trg_reported_need_reopening_guard ON reported_need")
    op.execute("DROP FUNCTION IF EXISTS guard_reported_need_reopening()")
    op.execute("DROP VIEW IF EXISTS effective_need_state")

    op.drop_index("ix_outcome_org_patient_recorded", table_name="outcome")
    op.drop_constraint("uq_outcome_organization_idempotency_key", "outcome", type_="unique")
    op.drop_constraint("fk_outcome_recorded_by_user", "outcome", type_="foreignkey")
    op.drop_constraint("fk_outcome_parent_need", "outcome", type_="foreignkey")
    op.drop_constraint("ck_outcome_disposition_state", "outcome", type_="check")
    op.drop_column("outcome", "idempotency_key")
    op.drop_column("outcome", "recorded_by_user_id")
    op.alter_column("outcome", "recorded_at", new_column_name="created_at")
    op.execute("UPDATE outcome SET note = '' WHERE note IS NULL")
    op.alter_column("outcome", "note", new_column_name="reason", nullable=False)
    op.alter_column("outcome", "disposition", new_column_name="status")
    op.execute("ALTER TYPE outcome_disposition RENAME TO outcome_status")
    op.create_check_constraint(
        "ck_outcome_status_state",
        "outcome",
        "status IN ('resolved', 'closed_unresolved')",
    )
    op.create_foreign_key(
        "fk_outcome_organization_reported_need",
        "outcome",
        "reported_need",
        ["organization_id", "reported_need_id"],
        ["organization_id", "id"],
    )
    op.create_index(
        "ix_outcome_org_patient_created",
        "outcome",
        ["organization_id", "patient_id", "created_at"],
    )

    op.drop_constraint("ck_navigation_task_cancellation_shape", "navigation_task", type_="check")
    op.drop_constraint("ck_navigation_task_assignment_shape", "navigation_task", type_="check")
    op.drop_constraint("ck_navigation_task_status_state", "navigation_task", type_="check")
    op.drop_constraint(
        "fk_navigation_task_cancelled_by_user", "navigation_task", type_="foreignkey"
    )
    op.drop_constraint("fk_navigation_task_parent_need", "navigation_task", type_="foreignkey")
    op.execute("UPDATE navigation_task SET status = 'open' WHERE status = 'assigned'")
    op.execute("ALTER TABLE navigation_task ALTER COLUMN status TYPE text USING status::text")
    op.execute("DROP TYPE navigation_task_status")
    op.execute(
        "CREATE TYPE navigation_task_status AS ENUM "
        "('open', 'in_progress', 'completed', 'cancelled')"
    )
    op.execute(
        "ALTER TABLE navigation_task ALTER COLUMN status TYPE navigation_task_status "
        "USING status::navigation_task_status"
    )
    op.drop_column("navigation_task", "cancellation_reason")
    op.drop_column("navigation_task", "cancelled_at")
    op.drop_column("navigation_task", "cancelled_by_user_id")
    op.alter_column("navigation_task", "reported_need_id", nullable=True)
    op.create_check_constraint(
        "ck_navigation_task_status_state",
        "navigation_task",
        "status IN ('open', 'in_progress', 'completed', 'cancelled')",
    )
    op.create_foreign_key(
        "fk_navigation_task_organization_reported_need",
        "navigation_task",
        "reported_need",
        ["organization_id", "reported_need_id"],
        ["organization_id", "id"],
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM reported_need WHERE reopened_from_need_id IS NOT NULL) THEN
                RAISE EXCEPTION 'Cannot downgrade 0003 while reopened reported needs exist';
            END IF;
        END $$
        """
    )
    op.drop_constraint("fk_reported_need_reopened_predecessor", "reported_need", type_="foreignkey")
    op.drop_constraint("fk_reported_need_origin_submission", "reported_need", type_="foreignkey")
    op.drop_constraint("uq_reported_need_reopened_from_need_id", "reported_need", type_="unique")
    op.drop_constraint("uq_reported_need_org_patient_id", "reported_need", type_="unique")
    op.drop_constraint("uq_reported_need_org_patient_episode_id", "reported_need", type_="unique")
    op.drop_constraint("ck_reported_need_origin", "reported_need", type_="check")
    op.drop_constraint("ck_reported_need_status_state", "reported_need", type_="check")
    op.execute("ALTER TABLE reported_need ALTER COLUMN status TYPE text USING status::text")
    op.execute("DROP TYPE need_status")
    op.execute("CREATE TYPE need_status AS ENUM ('open', 'in_progress', 'resolved', 'closed')")
    op.execute(
        "ALTER TABLE reported_need ALTER COLUMN status TYPE need_status USING status::need_status"
    )
    op.add_column("reported_need", sa.Column("resolved_at", sa.DateTime(timezone=True)))
    op.drop_column("reported_need", "reopened_from_need_id")
    op.drop_column("reported_need", "care_episode_id")
    op.alter_column("reported_need", "source_submission_id", nullable=False)
    op.create_check_constraint(
        "ck_reported_need_status_state",
        "reported_need",
        "status IN ('open', 'in_progress', 'resolved', 'closed')",
    )
    op.create_foreign_key(
        "fk_reported_need_organization_submission",
        "reported_need",
        "check_in_submission",
        ["organization_id", "source_submission_id"],
        ["organization_id", "id"],
    )
    op.drop_constraint(
        "uq_check_in_submission_org_patient_episode_id",
        "check_in_submission",
        type_="unique",
    )
    TASK_CANCELLATION_REASON.drop(op.get_bind(), checkfirst=False)
