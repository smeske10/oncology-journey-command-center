# ruff: noqa: E501

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from app.config import settings
from app.db.integrity import IntegrityViolation, inspect_integrity

PROJECT_ROOT = Path(__file__).resolve().parents[4]
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
DISPOSABLE_PREFIX = "ojcc_task7_"
BASE_TIME = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def _id(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"ojcc-integrity:{label}")


def _execute_batch(connection: Connection, sql: str, parameters: dict[str, Any]) -> None:
    for statement in sql.split(";"):
        if statement.strip():
            connection.execute(text(statement), parameters)


def _validate_local_url(url: URL) -> None:
    if url.get_backend_name() != "postgresql" or url.host not in LOOPBACK_HOSTS:
        raise ValueError("Restore-integrity tests require loopback PostgreSQL")


@contextmanager
def _disposable_database() -> Iterator[str]:
    configured = make_url(settings.database_url)
    _validate_local_url(configured)
    disposable = configured.set(database=f"{DISPOSABLE_PREFIX}{uuid4().hex}")
    database = disposable.database
    assert database is not None and database.startswith(DISPOSABLE_PREFIX)
    admin = disposable.set(database="postgres")
    engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    created = False
    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
        created = True
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "services/api/alembic.ini",
                "upgrade",
                "head",
            ],
            cwd=PROJECT_ROOT,
            env=os.environ | {"DATABASE_URL": disposable.render_as_string(hide_password=False)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        yield disposable.render_as_string(hide_password=False)
    finally:
        if created:
            assert database.startswith(DISPOSABLE_PREFIX)
            with engine.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :database AND pid <> pg_backend_pid()"
                    ),
                    {"database": database},
                )
                connection.execute(text(f'DROP DATABASE "{database}"'))
        engine.dispose()


@pytest.fixture(scope="module")
def integrity_database_url() -> Iterator[str]:
    with _disposable_database() as database_url:
        yield database_url


@pytest.fixture
def connection(integrity_database_url: str) -> Iterator[Connection]:
    engine = create_engine(integrity_database_url)
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        try:
            yield connection
        finally:
            transaction.rollback()
    engine.dispose()


def _seed_base(connection: Connection, label: str) -> dict[str, UUID]:
    ids = {
        name: _id(f"{label}:{name}")
        for name in (
            "organization",
            "user",
            "role",
            "patient",
            "episode",
            "pathway",
            "pathway_assignment",
            "definition",
            "submission",
            "need",
            "task",
            "outcome",
            "signal_rule",
            "signal",
            "policy",
            "proposal",
            "decision",
            "resolution",
            "audit",
        )
    }
    parameters: dict[str, Any] = ids | {
        "name": f"Integrity test {label}",
        "email": f"{label}@example.test",
        "external_ref": f"patient-{label}",
        "pathway_slug": f"pathway-{label}",
        "definition_slug": f"check-in-{label}",
        "rule_code": f"rule-{label}",
        "at": BASE_TIME,
        "granted_at": BASE_TIME - timedelta(days=30),
    }
    _execute_batch(
        connection,
        "INSERT INTO organization (id, name, created_at) VALUES (:organization, :name, :at); "
        "INSERT INTO user_account "
        "(id, primary_organization_id, email, display_name, is_active, created_at) VALUES "
        "(:user, :organization, :email, 'Synthetic Navigator', true, :at); "
        "INSERT INTO role_assignment "
        "(id, organization_id, user_id, role, granted_at, created_at) VALUES "
        "(:role, :organization, :user, 'navigator', :granted_at, :at); "
        "INSERT INTO synthetic_patient "
        "(id, organization_id, external_ref, display_name, demographics, created_at) VALUES "
        "(:patient, :organization, :external_ref, 'Synthetic Patient', '{}'::jsonb, :at); "
        "INSERT INTO care_episode "
        "(id, organization_id, patient_id, status, started_at) VALUES "
        "(:episode, :organization, :patient, 'active', :at); "
        "INSERT INTO pathway_definition "
        "(id, organization_id, slug, version, name, configuration, is_active, created_at) VALUES "
        "(:pathway, :organization, :pathway_slug, 1, 'Synthetic pathway', '{}'::jsonb, true, :at); "
        "INSERT INTO episode_pathway_assignment "
        "(id, organization_id, care_episode_id, pathway_definition_id, effective_from, "
        "migration_reason, authored_by_user_id, created_at) VALUES "
        "(:pathway_assignment, :organization, :episode, :pathway, :at, "
        "'Initial synthetic pathway', :user, :at); "
        "INSERT INTO check_in_definition "
        "(id, organization_id, pathway_definition_id, slug, version, title, questionnaire, created_at) VALUES "
        "(:definition, :organization, :pathway, :definition_slug, 1, 'Synthetic check-in', "
        "'{}'::jsonb, :at); "
        "INSERT INTO check_in_submission "
        "(id, organization_id, patient_id, care_episode_id, check_in_definition_id, status, answers, "
        "submission_source, submitted_by_user_id, submitted_at, created_at) VALUES "
        "(:submission, :organization, :patient, :episode, :definition, 'submitted', '{}'::jsonb, "
        "'clinician', :user, :at, :at); "
        "INSERT INTO reported_need "
        "(id, organization_id, patient_id, care_episode_id, source_submission_id, kind, status, evidence, created_at) VALUES "
        "(:need, :organization, :patient, :episode, :submission, 'transportation', 'open', '[]'::jsonb, :at); "
        "INSERT INTO signal_rule "
        "(id, organization_id, rule_code, version, rule_kind, name, created_at) VALUES "
        "(:signal_rule, :organization, :rule_code, 1, 'deterministic', 'Synthetic rule', :at)",
        parameters,
    )
    return ids


def _seed_signal(connection: Connection, ids: dict[str, UUID], *, acknowledged: bool = True) -> None:
    _execute_batch(
        connection,
        "INSERT INTO safety_signal "
        "(id, organization_id, patient_id, care_episode_id, source_submission_id, "
        "signal_rule_id, signal_rule_version, deterministic_level, effective_level, status, "
        "evidence, acknowledged_by_user_id, acknowledged_at, created_at) VALUES "
        "(:signal, :organization, :patient, :episode, :submission, :signal_rule, 1, "
        "'routine', 'routine', :status, '[]'::jsonb, :acknowledger, :acknowledged_at, :at)",
        ids
        | {
            "status": "acknowledged" if acknowledged else "open",
            "acknowledger": ids["user"] if acknowledged else None,
            "acknowledged_at": BASE_TIME + timedelta(hours=1) if acknowledged else None,
            "at": BASE_TIME,
        },
    )


def _seed_dismissal_proposal(
    connection: Connection, ids: dict[str, UUID], *, with_approval: bool
) -> None:
    _execute_batch(
        connection,
        "INSERT INTO approval_policy "
        "(id, organization_id, change_type, version, effective_from, "
        "deterministic_severity_threshold, allow_self_approval, required_approval_count, "
        "required_approver_role, created_at) VALUES "
        "(:policy, :organization, 'dismiss_signal', 1, :effective_from, 'urgent', true, 1, "
        "'navigator', :at); "
        "INSERT INTO proposed_change "
        "(id, organization_id, proposed_by_user_id, proposed_at, change_type, proposed_value, "
        "rationale, value_schema_id, value_schema_version, safety_signal_id, approval_policy_id, "
        "approval_policy_version, deterministic_severity_threshold_snapshot, "
        "allow_self_approval_snapshot, required_approval_count_snapshot, "
        "required_approver_role_snapshot) VALUES "
        "(:proposal, :organization, :user, :proposed_at, 'dismiss_signal', "
        "'{\"category\":\"false_positive\"}'::jsonb, 'Synthetic false positive', "
        "'ojcc.dismiss-signal', 1, :signal, :policy, 1, 'urgent', true, 1, 'navigator')",
        ids
        | {
            "effective_from": BASE_TIME - timedelta(days=60),
            "proposed_at": BASE_TIME + timedelta(hours=2),
            "at": BASE_TIME,
        },
    )
    if with_approval:
        connection.execute(
            text(
                "INSERT INTO approval_decision "
                "(id, organization_id, proposed_change_id, authorized_by_user_id, "
                "qualifying_role_assignment_id, qualifying_role_snapshot, decision, authorized_at) VALUES "
                "(:decision, :organization, :proposal, :user, :role, 'navigator', 'approved', :at)"
            ),
            ids | {"at": BASE_TIME + timedelta(hours=3)},
        )
    _execute_batch(
        connection,
        "UPDATE safety_signal SET dismissal_proposed_change_id = :proposal "
        "WHERE id = :signal",
        ids,
    )


def _violation(
    violations: list[IntegrityViolation], category: str, **identifiers: str
) -> IntegrityViolation:
    matches = [
        violation
        for violation in violations
        if violation.category == category and violation.identifiers == identifiers
    ]
    assert len(matches) == 1, violations
    return matches[0]


def test_reports_closed_need_with_active_task_and_preserves_corruption(
    connection: Connection,
) -> None:
    """Production break: restore audit misses a closed need that retains active work."""
    ids = _seed_base(connection, "closed-need")
    _execute_batch(
        connection,
        "INSERT INTO navigation_task "
        "(id, organization_id, patient_id, reported_need_id, title, status, created_at) VALUES "
        "(:task, :organization, :patient, :need, 'Call patient', 'open', :at); "
        "INSERT INTO outcome "
        "(id, organization_id, patient_id, reported_need_id, recorded_by_user_id, disposition, "
        "note, idempotency_key, recorded_at) VALUES "
        "(:outcome, :organization, :patient, :need, :user, 'resolved', 'Handled', "
        "'closed-need-outcome', :closed_at)",
        ids | {"at": BASE_TIME, "closed_at": BASE_TIME + timedelta(hours=1)},
    )

    violations = inspect_integrity(Session(bind=connection))

    violation = _violation(
        violations,
        "closed_need_non_terminal_task",
        reported_need_id=str(ids["need"]),
        navigation_task_id=str(ids["task"]),
        outcome_id=str(ids["outcome"]),
    )
    assert violation.evidence == {"task_status": "open"}
    assert connection.scalar(
        text("SELECT status::text FROM navigation_task WHERE id = :task"), ids
    ) == "open"


def test_reports_task_mutated_after_need_closure(connection: Connection) -> None:
    """Production break: restore audit misses a task completed after its need closed."""
    ids = _seed_base(connection, "task-after-close")
    closed_at = BASE_TIME + timedelta(hours=1)
    task_at = BASE_TIME + timedelta(hours=2)
    _execute_batch(
        connection,
            "INSERT INTO navigation_task "
            "(id, organization_id, patient_id, reported_need_id, title, status, created_at, "
            "completed_at) VALUES "
            "(:task, :organization, :patient, :need, 'Late completion', 'completed', :created_at, "
            ":task_at); "
            "INSERT INTO outcome "
            "(id, organization_id, patient_id, reported_need_id, recorded_by_user_id, disposition, "
            "idempotency_key, recorded_at) VALUES "
            "(:outcome, :organization, :patient, :need, :user, 'resolved', 'task-after-close', :closed_at)",
        ids | {"created_at": BASE_TIME, "closed_at": closed_at, "task_at": task_at},
    )

    violation = _violation(
        inspect_integrity(Session(bind=connection)),
        "task_after_need_closure",
        reported_need_id=str(ids["need"]),
        navigation_task_id=str(ids["task"]),
        outcome_id=str(ids["outcome"]),
    )
    assert violation.evidence == {
        "event": "completed",
        "need_closed_at": closed_at.isoformat(),
        "task_event_at": task_at.isoformat(),
        "task_status": "completed",
    }


def test_reports_missing_cancellation_audit_event(connection: Connection) -> None:
    """Production break: closure cancellation without its attributed audit event is accepted."""
    ids = _seed_base(connection, "missing-cancellation-audit")
    closed_at = BASE_TIME + timedelta(hours=1)
    _execute_batch(
        connection,
            "INSERT INTO navigation_task "
            "(id, organization_id, patient_id, reported_need_id, title, status, created_at, "
            "cancelled_by_user_id, cancelled_at, cancellation_reason) VALUES "
            "(:task, :organization, :patient, :need, 'Cancelled task', 'cancelled', :created_at, "
            ":user, :closed_at, 'need_closed'); "
            "INSERT INTO outcome "
            "(id, organization_id, patient_id, reported_need_id, recorded_by_user_id, disposition, "
            "idempotency_key, recorded_at) VALUES "
            "(:outcome, :organization, :patient, :need, :user, 'resolved', 'missing-audit', :closed_at)",
        ids | {"created_at": BASE_TIME, "closed_at": closed_at},
    )

    violation = _violation(
        inspect_integrity(Session(bind=connection)),
        "missing_task_cancellation_audit_event",
        reported_need_id=str(ids["need"]),
        navigation_task_id=str(ids["task"]),
        outcome_id=str(ids["outcome"]),
    )
    assert violation.evidence == {
        "issue": "missing_event",
        "expected_actor_user_id": str(ids["user"]),
        "expected_created_at": closed_at.isoformat(),
    }


def test_reports_signal_with_resolution_and_applied_dismissal(connection: Connection) -> None:
    """Production break: restore audit silently chooses one of two signal terminal records."""
    ids = _seed_base(connection, "dual-terminal-signal")
    _seed_signal(connection, ids)
    _seed_dismissal_proposal(connection, ids, with_approval=True)
    _execute_batch(
        connection,
        "INSERT INTO safety_signal_resolution "
        "(id, organization_id, safety_signal_id, resolved_by_user_id, resolved_at, resolution_reason) VALUES "
        "(:resolution, :organization, :signal, :user, :at, 'Handled by navigator')",
        ids | {"at": BASE_TIME + timedelta(hours=4)},
    )

    violation = _violation(
        inspect_integrity(Session(bind=connection)),
        "signal_dual_terminal_paths",
        safety_signal_id=str(ids["signal"]),
        safety_signal_resolution_id=str(ids["resolution"]),
        dismissal_proposed_change_id=str(ids["proposal"]),
    )
    assert violation.evidence == {"dismissal_state": "approved"}


def test_reports_applied_proposal_without_qualifying_approval(connection: Connection) -> None:
    """Production break: materialized application is trusted without current qualifying approval."""
    ids = _seed_base(connection, "unapproved-application")
    _seed_signal(connection, ids)
    _seed_dismissal_proposal(connection, ids, with_approval=False)

    violation = _violation(
        inspect_integrity(Session(bind=connection)),
        "applied_proposal_without_qualifying_approvals",
        proposed_change_id=str(ids["proposal"]),
        target_id=str(ids["signal"]),
    )
    assert violation.evidence == {
        "application": "signal_dismissal",
        "effective_state": "pending",
        "qualifying_approval_count": 0,
        "required_approval_count": 1,
    }


def test_reports_cross_organization_approval_qualification(connection: Connection) -> None:
    """Production break: a role assignment from another tenant qualifies an approval."""
    ids = _seed_base(connection, "cross-org-approval")
    _seed_signal(connection, ids)
    _seed_dismissal_proposal(connection, ids, with_approval=False)
    other = {
        "other_org": _id("cross-org-approval:other-org"),
        "other_user": _id("cross-org-approval:other-user"),
        "other_role": _id("cross-org-approval:other-role"),
    }
    _execute_batch(
        connection,
        "INSERT INTO organization (id, name, created_at) VALUES "
        "(:other_org, 'Other approval tenant', :at); "
        "INSERT INTO user_account "
        "(id, primary_organization_id, email, display_name, is_active, created_at) VALUES "
        "(:other_user, :other_org, 'other-approver@example.test', 'Other approver', true, :at); "
        "INSERT INTO role_assignment "
        "(id, organization_id, user_id, role, granted_at, created_at) VALUES "
        "(:other_role, :other_org, :other_user, 'navigator', :granted_at, :at); "
        "ALTER TABLE approval_decision DROP CONSTRAINT fk_approval_decision_qualifying_role; "
        "INSERT INTO approval_decision "
        "(id, organization_id, proposed_change_id, authorized_by_user_id, "
        "qualifying_role_assignment_id, qualifying_role_snapshot, decision, authorized_at) VALUES "
        "(:decision, :organization, :proposal, :other_user, :other_role, 'navigator', "
        "'approved', :authorized_at)",
        ids
        | other
        | {
            "at": BASE_TIME,
            "granted_at": BASE_TIME - timedelta(days=30),
            "authorized_at": BASE_TIME + timedelta(hours=3),
        },
    )

    violation = _violation(
        inspect_integrity(Session(bind=connection)),
        "cross_organization_approval",
        proposed_change_id=str(ids["proposal"]),
        approval_decision_id=str(ids["decision"]),
        qualifying_role_assignment_id=str(other["other_role"]),
    )
    assert violation.evidence == {
        "proposal_organization_id": str(ids["organization"]),
        "decision_organization_id": str(ids["organization"]),
        "role_assignment_organization_id": str(other["other_org"]),
    }
    invalid_qualification = _violation(
        inspect_integrity(Session(bind=connection)),
        "invalid_approval_qualification",
        proposed_change_id=str(ids["proposal"]),
        approval_decision_id=str(ids["decision"]),
        qualifying_role_assignment_id=str(other["other_role"]),
    )
    assert invalid_qualification.evidence == {
        "authorized_by_user_id": str(other["other_user"]),
        "role_assignment_user_id": None,
        "role_assignment_role": None,
        "required_role": "navigator",
        "granted_at": None,
        "revoked_at": None,
        "authorized_at": (BASE_TIME + timedelta(hours=3)).isoformat(),
    }


def test_reports_invalid_audit_actor_shape(connection: Connection) -> None:
    """Production break: restore audit accepts an actor discriminator with the wrong identity form."""
    ids = _seed_base(connection, "invalid-audit-actor")
    _execute_batch(
        connection,
        "ALTER TABLE audit_event DROP CONSTRAINT ck_audit_event_ck_audit_event_actor_shape; "
            "INSERT INTO audit_event "
            "(id, organization_id, actor_type, actor_policy_component, actor_policy_version, "
            "entity_type, entity_id, event_type, payload, created_at) VALUES "
            "(:audit, :organization, 'user', 'policy-engine', '1', 'reported_need', :need, "
            "'invalid_actor', '{}'::jsonb, :at)",
        ids | {"at": BASE_TIME},
    )

    violation = _violation(
        inspect_integrity(Session(bind=connection)),
        "invalid_audit_actor",
        audit_event_id=str(ids["audit"]),
    )
    assert violation.evidence == {
        "actor_type": "user",
        "actor_user_id": None,
        "actor_agent_run_id": None,
        "actor_policy_component": "policy-engine",
        "actor_policy_version": "1",
        "actor_system_component": None,
        "actor_system_version": None,
    }


def test_reports_overlapping_pathway_assignments(connection: Connection) -> None:
    """Production break: restore audit misses overlapping pathway authority intervals."""
    ids = _seed_base(connection, "overlapping-pathways")
    second_assignment = _id("overlapping-pathways:second-assignment")
    first_end = BASE_TIME + timedelta(days=30)
    second_start = BASE_TIME + timedelta(days=10)
    second_end = BASE_TIME + timedelta(days=40)
    _execute_batch(
        connection,
            "ALTER TABLE episode_pathway_assignment "
            "DROP CONSTRAINT ex_episode_pathway_assignment_no_overlap; "
            "UPDATE episode_pathway_assignment SET effective_to = :first_end "
            "WHERE id = :pathway_assignment; "
            "INSERT INTO episode_pathway_assignment "
            "(id, organization_id, care_episode_id, pathway_definition_id, effective_from, effective_to, "
            "migration_reason, authored_by_user_id, created_at) VALUES "
            "(:second_assignment, :organization, :episode, :pathway, :second_start, :second_end, "
            "'Invalid overlap', :user, :second_start)",
        ids
        | {
            "second_assignment": second_assignment,
            "first_end": first_end,
            "second_start": second_start,
            "second_end": second_end,
        },
    )

    violation = _violation(
        inspect_integrity(Session(bind=connection)),
        "overlapping_pathway_assignments",
        care_episode_id=str(ids["episode"]),
        first_assignment_id=str(ids["pathway_assignment"]),
        second_assignment_id=str(second_assignment),
    )
    assert violation.evidence == {
        "first_interval": [BASE_TIME.isoformat(), first_end.isoformat()],
        "second_interval": [second_start.isoformat(), second_end.isoformat()],
    }


@pytest.mark.parametrize(
    ("chain_type", "table_name", "column_name", "constraint_name"),
    [
        (
            "submission_correction",
            "check_in_submission",
            "supersedes_submission_id",
            "uq_check_in_submission_supersedes_submission_id",
        ),
        (
            "need_reopening",
            "reported_need",
            "reopened_from_need_id",
            "uq_reported_need_reopened_from_need_id",
        ),
        (
            "signal_escalation",
            "safety_signal",
            "escalated_from_signal_id",
            "uq_safety_signal_escalated_from_signal_id",
        ),
        (
            "proposal_revision",
            "proposed_change",
            "supersedes_proposed_change_id",
            "uq_proposed_change_supersedes_proposed_change_id",
        ),
    ],
)
def test_reports_every_forked_successor_chain(
    connection: Connection,
    chain_type: str,
    table_name: str,
    column_name: str,
    constraint_name: str,
) -> None:
    """Production break: restore audit accepts two successors for one immutable predecessor."""
    label = f"fork-{chain_type}"
    ids = _seed_base(connection, label)
    first = _id(f"{label}:first")
    second = _id(f"{label}:second")
    predecessor_by_chain = {
        "submission_correction": ids["submission"],
        "need_reopening": ids["need"],
        "signal_escalation": ids["signal"],
        "proposal_revision": ids["proposal"],
    }

    connection.execute(text(f"ALTER TABLE {table_name} DROP CONSTRAINT {constraint_name}"))
    if chain_type == "submission_correction":
        for successor, hour in ((first, 1), (second, 2)):
            connection.execute(
                text(
                    "INSERT INTO check_in_submission "
                    "(id, organization_id, patient_id, care_episode_id, check_in_definition_id, status, answers, "
                    "submission_source, submitted_by_user_id, supersedes_submission_id, submitted_at, created_at) VALUES "
                    "(:successor, :organization, :patient, :episode, :definition, 'submitted', '{}'::jsonb, "
                    "'clinician', :user, :submission, :at, :at)"
                ),
                ids | {"successor": successor, "at": BASE_TIME + timedelta(hours=hour)},
            )
    elif chain_type == "need_reopening":
        for successor in (first, second):
            connection.execute(
                text(
                    "INSERT INTO reported_need "
                    "(id, organization_id, patient_id, care_episode_id, reopened_from_need_id, kind, status, evidence, created_at) VALUES "
                    "(:successor, :organization, :patient, :episode, :need, 'transportation', 'open', '[]'::jsonb, :at)"
                ),
                ids | {"successor": successor, "at": BASE_TIME + timedelta(hours=1)},
            )
    elif chain_type == "signal_escalation":
        _seed_signal(connection, ids)
        escalation_rule = _id(f"{label}:escalation-rule")
        connection.execute(
            text(
                "INSERT INTO signal_rule "
                "(id, organization_id, rule_code, version, rule_kind, name, created_at) VALUES "
                "(:rule, :organization, :code, 1, 'human_escalation', 'Human escalation', :at)"
            ),
            ids | {"rule": escalation_rule, "code": f"escalation-{label}", "at": BASE_TIME},
        )
        for successor in (first, second):
            connection.execute(
                text(
                    "INSERT INTO safety_signal "
                    "(id, organization_id, patient_id, care_episode_id, escalated_from_signal_id, "
                    "signal_rule_id, signal_rule_version, deterministic_level, effective_level, status, evidence, created_at) VALUES "
                    "(:successor, :organization, :patient, :episode, :signal, :rule, 1, "
                    "'urgent', 'urgent', 'open', '[]'::jsonb, :at)"
                ),
                ids | {"successor": successor, "rule": escalation_rule, "at": BASE_TIME},
            )
    else:
        _seed_signal(connection, ids)
        _seed_dismissal_proposal(connection, ids, with_approval=False)
        for successor in (first, second):
            connection.execute(
                text(
                    "INSERT INTO proposed_change "
                    "(id, organization_id, proposed_by_user_id, proposed_at, change_type, proposed_value, "
                    "rationale, value_schema_id, value_schema_version, supersedes_proposed_change_id, "
                    "safety_signal_id, approval_policy_id, approval_policy_version, "
                    "deterministic_severity_threshold_snapshot, allow_self_approval_snapshot, "
                    "required_approval_count_snapshot, required_approver_role_snapshot) VALUES "
                    "(:successor, :organization, :user, :at, 'dismiss_signal', "
                    "'{\"category\":\"duplicate\"}'::jsonb, 'Revised synthetic proposal', "
                    "'ojcc.dismiss-signal', 1, :proposal, :signal, :policy, 1, 'urgent', true, 1, 'navigator')"
                ),
                ids | {"successor": successor, "at": BASE_TIME + timedelta(hours=4)},
            )

    predecessor = predecessor_by_chain[chain_type]
    violation = _violation(
        inspect_integrity(Session(bind=connection)),
        "forked_successor_chain",
        predecessor_id=str(predecessor),
    )
    assert violation.evidence == {
        "chain_type": chain_type,
        "successor_ids": sorted([str(first), str(second)]),
    }


def test_integrity_cli_exits_nonzero_and_emits_machine_readable_violation() -> None:
    """Production break: operators cannot fail a restore pipeline on detected corruption."""
    with _disposable_database() as database_url:
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL session_replication_role = replica"))
            ids = _seed_base(connection, "cli-failure")
            _execute_batch(
                connection,
                "INSERT INTO navigation_task "
                "(id, organization_id, patient_id, reported_need_id, title, status, created_at) VALUES "
                "(:task, :organization, :patient, :need, 'Active after close', 'open', :at); "
                "INSERT INTO outcome "
                "(id, organization_id, patient_id, reported_need_id, recorded_by_user_id, disposition, "
                "idempotency_key, recorded_at) VALUES "
                "(:outcome, :organization, :patient, :need, :user, 'resolved', 'cli-failure', :closed_at)",
                ids | {"at": BASE_TIME, "closed_at": BASE_TIME + timedelta(hours=1)},
            )
        engine.dispose()

        result = subprocess.run(
            [
                sys.executable,
                "services/api/scripts/check_integrity.py",
                "--database-url",
                database_url,
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 1, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert payload["status"] == "violations_found"
        assert payload["violation_count"] >= 1
        assert {
            "category": "closed_need_non_terminal_task",
            "identifiers": {
                "reported_need_id": str(ids["need"]),
                "navigation_task_id": str(ids["task"]),
                "outcome_id": str(ids["outcome"]),
            },
            "evidence": {"task_status": "open"},
        } in payload["violations"]
