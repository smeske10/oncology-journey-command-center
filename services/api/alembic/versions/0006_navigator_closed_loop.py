"""Add exact task authorization and durable follow-up records."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_navigator_closed_loop"
down_revision: str | None = "0005_workflow_knowledge_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FOLLOW_UP_RESPONSE_VALUE = postgresql.ENUM(
    "resolved",
    "unresolved",
    "still_needs_help",
    name="follow_up_response_value",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    FOLLOW_UP_RESPONSE_VALUE.create(bind, checkfirst=True)

    op.create_unique_constraint(
        "uq_proposed_change_org_navigation_task_id",
        "proposed_change",
        ["organization_id", "navigation_task_id", "id"],
    )
    op.create_unique_constraint(
        "uq_navigation_task_org_patient_need_id",
        "navigation_task",
        ["organization_id", "patient_id", "reported_need_id", "id"],
    )
    op.create_unique_constraint(
        "uq_patient_identity_link_organization_user_id",
        "patient_identity_link",
        ["organization_id", "user_id", "id"],
    )
    op.add_column(
        "navigation_task",
        sa.Column("authorized_proposed_change_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_navigation_task_authorized_proposed_change",
        "navigation_task",
        "proposed_change",
        ["organization_id", "id", "authorized_proposed_change_id"],
        ["organization_id", "navigation_task_id", "id"],
    )

    op.create_table(
        "follow_up_request",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("care_episode_id", sa.Uuid(), nullable=False),
        sa.Column("reported_need_id", sa.Uuid(), nullable=False),
        sa.Column("navigation_task_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_follow_up_request"),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name="uq_follow_up_request_organization_id_id",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "navigation_task_id",
            name="uq_follow_up_request_organization_navigation_task",
        ),
        sa.CheckConstraint(
            "prompt_version = 1",
            name="ck_follow_up_request_prompt_version",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_follow_up_request_organization_id_organization",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["user_account.id"],
            name="fk_follow_up_request_requested_by_user",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "patient_id", "care_episode_id", "reported_need_id"],
            [
                "reported_need.organization_id",
                "reported_need.patient_id",
                "reported_need.care_episode_id",
                "reported_need.id",
            ],
            name="fk_follow_up_request_reported_need",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "patient_id", "reported_need_id", "navigation_task_id"],
            [
                "navigation_task.organization_id",
                "navigation_task.patient_id",
                "navigation_task.reported_need_id",
                "navigation_task.id",
            ],
            name="fk_follow_up_request_navigation_task",
        ),
    )
    op.create_index(
        "ix_follow_up_request_org_patient_requested",
        "follow_up_request",
        ["organization_id", "patient_id", "requested_at", "id"],
    )
    op.create_index(
        "ix_follow_up_request_org_need_requested",
        "follow_up_request",
        ["organization_id", "reported_need_id", "requested_at", "id"],
    )

    op.create_table(
        "follow_up_response",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("follow_up_request_id", sa.Uuid(), nullable=False),
        sa.Column("submitted_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("patient_identity_link_id", sa.Uuid(), nullable=False),
        sa.Column("response", FOLLOW_UP_RESPONSE_VALUE, nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_follow_up_response"),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name="uq_follow_up_response_organization_id_id",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "follow_up_request_id",
            name="uq_follow_up_response_organization_request",
        ),
        sa.CheckConstraint(
            "response IN ('resolved', 'unresolved', 'still_needs_help')",
            name="ck_follow_up_response_response_state",
        ),
        sa.CheckConstraint(
            "note IS NULL OR char_length(note) <= 2000",
            name="ck_follow_up_response_note_length",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_follow_up_response_organization_id_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "follow_up_request_id"],
            ["follow_up_request.organization_id", "follow_up_request.id"],
            name="fk_follow_up_response_request",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "submitted_by_user_id", "patient_identity_link_id"],
            [
                "patient_identity_link.organization_id",
                "patient_identity_link.user_id",
                "patient_identity_link.id",
            ],
            name="fk_follow_up_response_patient_identity_link",
        ),
    )
    _replace_navigation_task_guard()
    _create_bound_navigation_task_delete_guard()
    _create_follow_up_guards()
    _create_patient_identity_link_history_guard()
    _create_navigation_task_transition_audit()
    _configure_application_role_surface()


def _replace_navigation_task_guard() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_navigation_task_lifecycle()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE parent_need_id uuid;
        DECLARE closure outcome%ROWTYPE;
        DECLARE proposal proposed_change%ROWTYPE;
        DECLARE proposal_state text;
        DECLARE qualifying_role_id uuid;
        DECLARE transition_at timestamptz := clock_timestamp();
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

            IF TG_OP = 'INSERT' AND NEW.authorized_proposed_change_id IS NOT NULL THEN
                RAISE EXCEPTION 'Navigation task cannot be inserted already bound';
            END IF;

            IF TG_OP = 'UPDATE'
               AND OLD.authorized_proposed_change_id IS NULL
               AND NEW.authorized_proposed_change_id IS NOT NULL THEN
                IF OLD.status <> 'open' OR NEW.status <> 'assigned'
                   OR OLD.assignee_user_id IS NOT NULL
                   OR NEW.assignee_user_id IS NULL
                   OR NEW.due_at IS NULL THEN
                    RAISE EXCEPTION
                        'Navigation task binding requires open to assigned with owner and due time';
                END IF;
                IF NEW.due_at <= transition_at THEN
                    RAISE EXCEPTION 'Navigation task due time must be in the future';
                END IF;

                SELECT candidate.* INTO proposal
                FROM proposed_change AS candidate
                WHERE candidate.organization_id = NEW.organization_id
                  AND candidate.navigation_task_id = NEW.id
                  AND candidate.id = NEW.authorized_proposed_change_id
                FOR UPDATE;

                IF proposal.id IS NULL
                   OR proposal.change_type <> 'authorize_navigation_task'
                   OR proposal.value_schema_id <> 'ojcc.authorize-navigation-task'
                   OR proposal.value_schema_version NOT IN (1, 2) THEN
                    RAISE EXCEPTION
                        'Navigation task binding requires its exact supported task proposal';
                END IF;

                SELECT effective_state INTO proposal_state
                FROM effective_proposed_change_state
                WHERE organization_id = NEW.organization_id
                  AND id = proposal.id;
                IF proposal_state IS DISTINCT FROM 'approved' THEN
                    RAISE EXCEPTION 'Navigation task proposal must be approved before binding';
                END IF;
                IF NEW.title IS DISTINCT FROM proposal.proposed_value ->> 'title' THEN
                    RAISE EXCEPTION 'Navigation task title must match the approved proposal';
                END IF;

                SELECT assignment.id INTO qualifying_role_id
                FROM role_assignment AS assignment
                WHERE assignment.organization_id = NEW.organization_id
                  AND assignment.user_id = NEW.assignee_user_id
                  AND assignment.role = 'navigator'
                  AND assignment.granted_at <= transition_at
                  AND (assignment.revoked_at IS NULL OR transition_at < assignment.revoked_at)
                ORDER BY assignment.granted_at DESC, assignment.id ASC
                LIMIT 1
                FOR UPDATE;
                IF qualifying_role_id IS NULL THEN
                    RAISE EXCEPTION 'Navigation task owner requires active navigator authority';
                END IF;
            ELSIF TG_OP = 'UPDATE' AND OLD.authorized_proposed_change_id IS NOT NULL THEN
                IF NEW.authorized_proposed_change_id IS DISTINCT FROM
                   OLD.authorized_proposed_change_id
                   OR NEW.title IS DISTINCT FROM OLD.title
                   OR NEW.assignee_user_id IS DISTINCT FROM OLD.assignee_user_id
                   OR NEW.due_at IS DISTINCT FROM OLD.due_at THEN
                    RAISE EXCEPTION
                        'Navigation task binding, title, owner, and due time '
                        'are frozen after claim';
                END IF;

                IF NEW.status IS DISTINCT FROM OLD.status THEN
                    IF NOT (
                        (OLD.status = 'assigned' AND NEW.status = 'in_progress') OR
                        (OLD.status = 'in_progress' AND NEW.status = 'completed')
                    ) THEN
                        RAISE EXCEPTION 'Bound navigation task transition is not allowed';
                    END IF;

                    SELECT assignment.id INTO qualifying_role_id
                    FROM role_assignment AS assignment
                    WHERE assignment.organization_id = NEW.organization_id
                      AND assignment.user_id = NEW.assignee_user_id
                      AND assignment.role = 'navigator'
                      AND assignment.granted_at <= transition_at
                      AND (assignment.revoked_at IS NULL OR transition_at < assignment.revoked_at)
                    ORDER BY assignment.granted_at DESC, assignment.id ASC
                    LIMIT 1
                    FOR UPDATE;
                    IF qualifying_role_id IS NULL THEN
                        RAISE EXCEPTION 'Navigation task owner requires active navigator authority';
                    END IF;

                    IF NEW.status = 'completed' THEN
                        NEW.completed_at := transition_at;
                    END IF;
                ELSIF NEW.completed_at IS DISTINCT FROM OLD.completed_at THEN
                    RAISE EXCEPTION 'Navigation task completion time is immutable';
                END IF;
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


def _create_navigation_task_transition_audit() -> None:
    op.execute(
        """
        CREATE FUNCTION record_navigation_task_transition()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE transition_event_type text;
        DECLARE transition_at timestamptz;
        DECLARE follow_up_request_id uuid;
        DECLARE need_episode_id uuid;
        DECLARE event_payload jsonb;
        BEGIN
            IF OLD.status = 'open'
               AND NEW.status = 'assigned'
               AND OLD.authorized_proposed_change_id IS NULL
               AND NEW.authorized_proposed_change_id IS NOT NULL THEN
                transition_event_type := 'navigation_task_claimed';
                transition_at := clock_timestamp();
            ELSIF OLD.status = 'assigned'
                  AND NEW.status = 'in_progress'
                  AND NEW.authorized_proposed_change_id IS NOT NULL THEN
                transition_event_type := 'navigation_task_started';
                transition_at := clock_timestamp();
            ELSIF OLD.status = 'in_progress'
                  AND NEW.status = 'completed'
                  AND NEW.authorized_proposed_change_id IS NOT NULL THEN
                transition_event_type := 'navigation_task_completed';
                transition_at := NEW.completed_at;
                follow_up_request_id := md5(
                    NEW.id::text || 'follow_up_request'
                )::uuid;

                SELECT need.care_episode_id INTO need_episode_id
                FROM reported_need AS need
                WHERE need.organization_id = NEW.organization_id
                  AND need.patient_id = NEW.patient_id
                  AND need.id = NEW.reported_need_id;

                INSERT INTO follow_up_request
                    (id, organization_id, patient_id, care_episode_id,
                     reported_need_id, navigation_task_id, requested_by_user_id,
                     requested_at, prompt_version)
                VALUES
                    (follow_up_request_id, NEW.organization_id, NEW.patient_id,
                     need_episode_id, NEW.reported_need_id, NEW.id,
                     NEW.assignee_user_id, transition_at, 1);
            ELSE
                RETURN NEW;
            END IF;

            event_payload := jsonb_build_object(
                'need_id', NEW.reported_need_id::text,
                'authorized_proposed_change_id', NEW.authorized_proposed_change_id::text,
                'from_status', OLD.status::text,
                'to_status', NEW.status::text,
                'assignee_user_id', NEW.assignee_user_id::text,
                'due_at', NEW.due_at
            );
            IF follow_up_request_id IS NOT NULL THEN
                event_payload := event_payload || jsonb_build_object(
                    'follow_up_request_id', follow_up_request_id::text
                );
            END IF;

            INSERT INTO audit_event
                (id, organization_id, actor_type, actor_user_id, entity_type,
                 entity_id, event_type, payload, created_at)
            VALUES
                (md5(NEW.id::text || transition_event_type)::uuid,
                 NEW.organization_id, 'user', NEW.assignee_user_id,
                 'navigation_task', NEW.id, transition_event_type,
                 event_payload, transition_at);
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_navigation_task_transition_audit
        AFTER UPDATE OF status ON navigation_task
        FOR EACH ROW
        EXECUTE FUNCTION record_navigation_task_transition()
        """
    )


def _create_bound_navigation_task_delete_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION guard_bound_navigation_task_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.authorized_proposed_change_id IS NOT NULL THEN
                RAISE EXCEPTION 'bound navigation task cannot be deleted';
            END IF;
            RETURN OLD;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_bound_navigation_task_delete_guard
        BEFORE DELETE ON navigation_task
        FOR EACH ROW
        EXECUTE FUNCTION guard_bound_navigation_task_delete()
        """
    )


def _create_follow_up_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION guard_follow_up_request_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE task_record navigation_task%ROWTYPE;
        DECLARE need_episode_id uuid;
        BEGIN
            SELECT task.* INTO task_record
            FROM navigation_task AS task
            WHERE task.organization_id = NEW.organization_id
              AND task.patient_id = NEW.patient_id
              AND task.reported_need_id = NEW.reported_need_id
              AND task.id = NEW.navigation_task_id;

            IF task_record.id IS NULL
               OR task_record.status <> 'completed'
               OR task_record.authorized_proposed_change_id IS NULL THEN
                RAISE EXCEPTION 'follow-up request requires a completed bound task';
            END IF;

            SELECT need.care_episode_id INTO need_episode_id
            FROM reported_need AS need
            WHERE need.organization_id = NEW.organization_id
              AND need.patient_id = NEW.patient_id
              AND need.id = NEW.reported_need_id;

            IF need_episode_id IS NULL
               OR NEW.care_episode_id IS DISTINCT FROM need_episode_id
               OR NEW.requested_by_user_id IS DISTINCT FROM task_record.assignee_user_id
               OR NEW.requested_at IS DISTINCT FROM task_record.completed_at
               OR NEW.prompt_version <> 1 THEN
                RAISE EXCEPTION 'follow-up request does not match completed bound task';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_follow_up_request_insert_guard
        BEFORE INSERT ON follow_up_request
        FOR EACH ROW
        EXECUTE FUNCTION guard_follow_up_request_insert()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_follow_up_response_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE request_record follow_up_request%ROWTYPE;
        DECLARE link_record patient_identity_link%ROWTYPE;
        DECLARE response_at timestamptz;
        DECLARE qualifying_role_id uuid;
        BEGIN
            SELECT request.* INTO request_record
            FROM follow_up_request AS request
            WHERE request.organization_id = NEW.organization_id
              AND request.id = NEW.follow_up_request_id;
            IF request_record.id IS NULL THEN
                RAISE EXCEPTION 'follow-up request is not available';
            END IF;

            PERFORM need.id
            FROM reported_need AS need
            WHERE need.organization_id = request_record.organization_id
              AND need.patient_id = request_record.patient_id
              AND need.id = request_record.reported_need_id
            FOR UPDATE;

            SELECT request.* INTO request_record
            FROM follow_up_request AS request
            WHERE request.organization_id = NEW.organization_id
              AND request.id = NEW.follow_up_request_id
            FOR UPDATE;

            IF EXISTS (
                SELECT 1 FROM outcome
                WHERE organization_id = request_record.organization_id
                  AND reported_need_id = request_record.reported_need_id
            ) THEN
                RAISE EXCEPTION 'follow-up response cannot be recorded after need closure';
            END IF;

            response_at := clock_timestamp();
            SELECT link.* INTO link_record
            FROM patient_identity_link AS link
            WHERE link.organization_id = NEW.organization_id
              AND link.user_id = NEW.submitted_by_user_id
              AND link.id = NEW.patient_identity_link_id
            FOR UPDATE;
            IF link_record.id IS NULL
               OR link_record.patient_id IS DISTINCT FROM request_record.patient_id
               OR link_record.linked_at > response_at
               OR (
                    link_record.revoked_at IS NOT NULL
                    AND response_at >= link_record.revoked_at
               ) THEN
                RAISE EXCEPTION 'follow-up response requires an active matching patient link';
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM user_account
                WHERE id = NEW.submitted_by_user_id AND is_active
            ) THEN
                RAISE EXCEPTION 'follow-up response requires an active user';
            END IF;

            SELECT assignment.id INTO qualifying_role_id
            FROM role_assignment AS assignment
            WHERE assignment.organization_id = NEW.organization_id
              AND assignment.user_id = NEW.submitted_by_user_id
              AND assignment.role = 'supporting_actor'
              AND assignment.granted_at <= response_at
              AND (assignment.revoked_at IS NULL OR response_at < assignment.revoked_at)
            ORDER BY assignment.granted_at DESC, assignment.id ASC
            LIMIT 1
            FOR UPDATE;
            IF qualifying_role_id IS NULL THEN
                RAISE EXCEPTION 'follow-up response requires current supporting actor authority';
            END IF;

            NEW.submitted_at := response_at;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_follow_up_response_insert_guard
        BEFORE INSERT ON follow_up_response
        FOR EACH ROW
        EXECUTE FUNCTION guard_follow_up_response_insert()
        """
    )
    for table_name in ("follow_up_request", "follow_up_response"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_append_only
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION reject_append_only_mutation()
            """
        )


def _create_patient_identity_link_history_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION guard_patient_identity_link_response_history()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM follow_up_response AS response
                WHERE response.organization_id = OLD.organization_id
                  AND response.submitted_by_user_id = OLD.user_id
                  AND response.patient_identity_link_id = OLD.id
            ) THEN
                IF NEW.organization_id IS DISTINCT FROM OLD.organization_id
                   OR NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.user_id IS DISTINCT FROM OLD.user_id
                   OR NEW.patient_id IS DISTINCT FROM OLD.patient_id
                   OR NEW.linked_at IS DISTINCT FROM OLD.linked_at THEN
                    RAISE EXCEPTION 'patient link response attribution is immutable';
                END IF;

                IF NEW.revoked_at IS NOT NULL AND EXISTS (
                    SELECT 1
                    FROM follow_up_response AS response
                    WHERE response.organization_id = OLD.organization_id
                      AND response.submitted_by_user_id = OLD.user_id
                      AND response.patient_identity_link_id = OLD.id
                      AND response.submitted_at >= NEW.revoked_at
                ) THEN
                    RAISE EXCEPTION 'patient link response attribution cannot be backdated';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_patient_identity_link_response_history
        BEFORE UPDATE ON patient_identity_link
        FOR EACH ROW
        EXECUTE FUNCTION guard_patient_identity_link_response_history()
        """
    )


def _configure_application_role_surface() -> None:
    for function_name in (
        "guard_navigation_task_lifecycle",
        "guard_bound_navigation_task_delete",
        "guard_follow_up_request_insert",
        "guard_follow_up_response_insert",
        "guard_patient_identity_link_response_history",
        "record_navigation_task_transition",
    ):
        op.execute(f"ALTER FUNCTION public.{function_name}() SECURITY DEFINER")
        op.execute(
            f"ALTER FUNCTION public.{function_name}() "
            "SET search_path = pg_catalog, public, pg_temp"
        )
        op.execute(f"REVOKE ALL ON FUNCTION public.{function_name}() FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{function_name}() TO ojcc_app")

    op.execute("REVOKE ALL PRIVILEGES ON TABLE follow_up_request FROM ojcc_app")
    op.execute("GRANT SELECT ON TABLE follow_up_request TO ojcc_app")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE follow_up_response FROM ojcc_app")
    op.execute("GRANT SELECT, INSERT ON TABLE follow_up_response TO ojcc_app")


def downgrade() -> None:
    bind = op.get_bind()
    has_history = bind.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1 FROM navigation_task
                WHERE authorized_proposed_change_id IS NOT NULL
            ) OR EXISTS (SELECT 1 FROM follow_up_request)
              OR EXISTS (SELECT 1 FROM follow_up_response)
            """
        )
    ).scalar_one()
    if has_history:
        raise RuntimeError(
            "Refusing to downgrade 0006: approved task bindings or follow-up history "
            "exist. Preserve the database and export that history before retrying."
        )

    op.execute("REVOKE ALL PRIVILEGES ON TABLE follow_up_response FROM ojcc_app")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE follow_up_request FROM ojcc_app")

    op.execute(
        "DROP TRIGGER trg_patient_identity_link_response_history "
        "ON patient_identity_link"
    )
    op.execute("DROP TRIGGER trg_follow_up_response_append_only ON follow_up_response")
    op.execute("DROP TRIGGER trg_follow_up_response_insert_guard ON follow_up_response")
    op.execute("DROP TRIGGER trg_follow_up_request_append_only ON follow_up_request")
    op.execute("DROP TRIGGER trg_follow_up_request_insert_guard ON follow_up_request")
    op.execute("DROP TRIGGER trg_navigation_task_transition_audit ON navigation_task")
    op.execute("DROP TRIGGER trg_bound_navigation_task_delete_guard ON navigation_task")

    for function_name in (
        "guard_patient_identity_link_response_history",
        "guard_follow_up_response_insert",
        "guard_follow_up_request_insert",
        "record_navigation_task_transition",
        "guard_bound_navigation_task_delete",
    ):
        op.execute(f"DROP FUNCTION public.{function_name}()")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_navigation_task_lifecycle()
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
        "ALTER FUNCTION public.guard_navigation_task_lifecycle() SECURITY INVOKER"
    )
    op.execute("ALTER FUNCTION public.guard_navigation_task_lifecycle() RESET ALL")
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.guard_navigation_task_lifecycle() TO PUBLIC"
    )

    op.drop_table("follow_up_response")
    op.drop_table("follow_up_request")
    op.drop_constraint(
        "fk_navigation_task_authorized_proposed_change",
        "navigation_task",
        type_="foreignkey",
    )
    op.drop_column("navigation_task", "authorized_proposed_change_id")
    op.drop_constraint(
        "uq_patient_identity_link_organization_user_id",
        "patient_identity_link",
        type_="unique",
    )
    op.drop_constraint(
        "uq_navigation_task_org_patient_need_id",
        "navigation_task",
        type_="unique",
    )
    op.drop_constraint(
        "uq_proposed_change_org_navigation_task_id",
        "proposed_change",
        type_="unique",
    )
    FOLLOW_UP_RESPONSE_VALUE.drop(bind, checkfirst=True)
