# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from app.db.integrity import inspect_integrity

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
DISPOSABLE_DATABASE_PATTERN = re.compile(r"^ojcc_(?:demo|task7)_[0-9a-f]{8,32}$")
SEED_TIME = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)


def _id(name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"ojcc-demo:{name}")


DEMO_IDS = {
    name: _id(name)
    for name in (
        "organization",
        "patient_user",
        "navigator_user",
        "administrator_user",
        "patient_role",
        "navigator_historical_role",
        "navigator_role",
        "administrator_role",
        "patient",
        "patient_identity_link",
        "episode",
        "pathway_v1",
        "pathway_v2",
        "pathway_assignment_v1",
        "pathway_assignment_v2",
        "definition_v1",
        "definition_v2",
        "submission_v1",
        "submission_v2",
        "closed_need",
        "open_need",
        "cancelled_task",
        "open_task",
        "outcome",
        "deterministic_rule",
        "human_escalation_rule",
        "dismissed_signal",
        "recovery_signal",
        "acknowledged_signal",
        "resolved_signal",
        "signal_resolution",
        "dismiss_policy",
        "override_policy",
        "task_policy",
        "message_policy",
        "patient_message",
        "dismiss_proposal",
        "override_proposal",
        "task_proposal",
        "message_proposal",
        "declined_proposal",
        "revised_proposal",
        "dismiss_decision",
        "override_decision",
        "task_decision",
        "message_decision",
        "decline_decision",
        "resource",
        "task_resource",
        "workflow",
        "transition_triaged",
        "transition_complete",
        "successful_agent_run",
        "failed_agent_run",
        "manual_review",
        "knowledge_document",
        "knowledge_approval",
        "citation",
        "audit_user",
        "audit_agent",
        "audit_policy",
        "audit_system",
    )
}


@dataclass(frozen=True)
class SeedSummary:
    organization_id: UUID
    row_counts: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "organization_id": str(self.organization_id),
            "row_counts": self.row_counts,
        }


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


TIMES = {
    "seed": SEED_TIME,
    "pathway_change": _at("2026-02-02T12:00:00+00:00"),
    "submission_v1_at": _at("2026-02-03T09:00:00+00:00"),
    "submission_v2_at": _at("2026-02-04T09:00:00+00:00"),
    "need_closed": _at("2026-02-05T10:00:00+00:00"),
    "need_reopened": _at("2026-02-06T10:00:00+00:00"),
    "acknowledged": _at("2026-02-04T10:00:00+00:00"),
    "resolved": _at("2026-02-04T11:00:00+00:00"),
    "workflow_started": _at("2026-02-04T09:05:00+00:00"),
    "transition_triaged_at": _at("2026-02-04T09:10:00+00:00"),
    "transition_complete_at": _at("2026-02-04T09:20:00+00:00"),
    "agent_run_at": _at("2026-02-04T09:25:00+00:00"),
    "proposed": _at("2026-02-04T12:00:00+00:00"),
    "approved": _at("2026-02-04T13:00:00+00:00"),
    "delivered": _at("2026-02-04T14:00:00+00:00"),
}

ROW_COUNT_TABLES = (
    "agent_run",
    "agent_run_citation",
    "approval_decision",
    "approval_policy",
    "audit_event",
    "care_episode",
    "check_in_definition",
    "check_in_submission",
    "episode_pathway_assignment",
    "knowledge_document",
    "manual_review_task",
    "navigation_task",
    "navigation_task_resource",
    "organization",
    "organization_knowledge_approval",
    "outcome",
    "patient_identity_link",
    "patient_message",
    "pathway_definition",
    "proposed_change",
    "reported_need",
    "resource",
    "role_assignment",
    "safety_signal",
    "safety_signal_resolution",
    "signal_rule",
    "synthetic_patient",
    "user_account",
    "workflow_run",
    "workflow_transition_event",
)


def _insert(session: Session, sql: str, parameters: dict[str, Any]) -> None:
    statement = sql.replace("ON CONFLICT DO NOTHING", "ON CONFLICT (id) DO NOTHING")
    session.execute(text(statement), parameters)


def _row_counts(session: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in ROW_COUNT_TABLES:
        if table == "organization":
            predicate = "id = :organization_id"
        elif table == "user_account":
            predicate = "primary_organization_id = :organization_id"
        else:
            predicate = "organization_id = :organization_id"
        counts[table] = int(
            session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE {predicate}"),
                {"organization_id": DEMO_IDS["organization"]},
            )
            or 0
        )
    return counts


def seed_demo(session: Session) -> SeedSummary:
    """Insert one fixed, entirely synthetic and idempotent reconciled-domain dataset."""
    ids = DEMO_IDS
    values: dict[str, Any] = ids | TIMES
    session.execute(text("SET LOCAL session_replication_role = replica"))

    _insert(
        session,
        "INSERT INTO organization (id, name, created_at) VALUES "
        "(:organization, 'Oncology Journey Command Center Synthetic Demo', :seed) "
        "ON CONFLICT DO NOTHING",
        values,
    )
    users = (
        ("patient_user", "synthetic.patient@example.test", "Synthetic Patient User"),
        ("navigator_user", "synthetic.navigator@example.test", "Synthetic Navigator"),
        ("administrator_user", "synthetic.admin@example.test", "Synthetic Administrator"),
    )
    for user_key, email, display_name in users:
        _insert(
            session,
            "INSERT INTO user_account "
            "(id, primary_organization_id, email, display_name, is_active, created_at) VALUES "
            "(:id, :organization, :email, :display_name, true, :seed) ON CONFLICT DO NOTHING",
            values | {"id": ids[user_key], "email": email, "display_name": display_name},
        )

    role_rows = (
        ("patient_role", "patient_user", "supporting_actor", SEED_TIME, None),
        (
            "navigator_historical_role",
            "navigator_user",
            "navigator",
            _at("2025-12-01T12:00:00+00:00"),
            _at("2026-01-15T12:00:00+00:00"),
        ),
        ("navigator_role", "navigator_user", "navigator", _at("2026-01-16T12:00:00+00:00"), None),
        ("administrator_role", "administrator_user", "administrator", SEED_TIME, None),
    )
    for role_key, user_key, role, granted_at, revoked_at in role_rows:
        _insert(
            session,
            "INSERT INTO role_assignment "
            "(id, organization_id, user_id, role, granted_at, revoked_at, created_at) VALUES "
            "(:id, :organization, :user_id, :role, :granted_at, :revoked_at, :seed) "
            "ON CONFLICT DO NOTHING",
            values
            | {
                "id": ids[role_key],
                "user_id": ids[user_key],
                "role": role,
                "granted_at": granted_at,
                "revoked_at": revoked_at,
            },
        )

    _insert(
        session,
        "INSERT INTO synthetic_patient "
        "(id, organization_id, external_ref, display_name, birth_date, demographics, created_at) VALUES "
        "(:patient, :organization, 'SYNTHETIC-PATIENT-001', 'Synthetic Patient', '1980-06-15', "
        "CAST(:demographics AS jsonb), :seed) ON CONFLICT DO NOTHING",
        values
        | {
            "demographics": json.dumps(
                {
                    "diagnosis": "Synthetic breast cancer journey",
                    "consent_status": "synthetic demo consented",
                    "upcoming_appointment": {
                        "label": "Synthetic oncology follow-up",
                        "starts_at": "2026-02-10T15:00:00Z",
                    },
                },
                sort_keys=True,
            )
        },
    )
    _insert(
        session,
        "INSERT INTO patient_identity_link "
        "(id, organization_id, user_id, patient_id, linked_at, created_at) VALUES "
        "(:patient_identity_link, :organization, :patient_user, :patient, :seed, :seed) "
        "ON CONFLICT DO NOTHING",
        values,
    )
    _insert(
        session,
        "INSERT INTO care_episode "
        "(id, organization_id, patient_id, status, started_at) VALUES "
        "(:episode, :organization, :patient, 'active', :seed) ON CONFLICT DO NOTHING",
        values,
    )

    for version, pathway_key in ((1, "pathway_v1"), (2, "pathway_v2")):
        _insert(
            session,
            "INSERT INTO pathway_definition "
            "(id, organization_id, slug, version, name, configuration, is_active, created_at) VALUES "
            "(:id, :organization, 'synthetic-breast-cancer', :version, :name, "
            "CAST(:configuration AS jsonb), :is_active, :seed) ON CONFLICT DO NOTHING",
            values
            | {
                "id": ids[pathway_key],
                "version": version,
                "name": f"Synthetic breast cancer pathway v{version}",
                "configuration": json.dumps({"synthetic": True, "version": version}),
                "is_active": version == 2,
            },
        )
    assignments = (
        (
            "pathway_assignment_v1",
            "pathway_v1",
            SEED_TIME,
            TIMES["pathway_change"],
            "Initial synthetic pathway",
        ),
        (
            "pathway_assignment_v2",
            "pathway_v2",
            TIMES["pathway_change"],
            None,
            "Synthetic pathway version update",
        ),
    )
    for assignment_key, pathway_key, effective_from, effective_to, reason in assignments:
        _insert(
            session,
            "INSERT INTO episode_pathway_assignment "
            "(id, organization_id, care_episode_id, pathway_definition_id, effective_from, "
            "effective_to, migration_reason, authored_by_user_id, created_at) VALUES "
            "(:id, :organization, :episode, :pathway, :effective_from, :effective_to, :reason, "
            ":navigator_user, :effective_from) ON CONFLICT DO NOTHING",
            values
            | {
                "id": ids[assignment_key],
                "pathway": ids[pathway_key],
                "effective_from": effective_from,
                "effective_to": effective_to,
                "reason": reason,
            },
        )

    questionnaires: dict[int, dict[str, Any]] = {}
    for version, definition_key, pathway_key in (
        (1, "definition_v1", "pathway_v1"),
        (2, "definition_v2", "pathway_v2"),
    ):
        questionnaire = {
            "canonical": (
                "https://oncology-journey-command-center.example/Questionnaire/"
                f"weekly-synthetic-check-in|{version}"
            ),
            "questions": [
                {
                    "link_id": "pain_change",
                    "label": "Since your last check-in, is synthetic pain better, the same, or worse?",
                    "options": [
                        {"value": "better", "label": "It is better"},
                        {"value": "same", "label": "About the same"},
                        {"value": "worse", "label": "It is worse"},
                    ],
                    "required": True,
                },
                {
                    "link_id": "transportation",
                    "label": "Need synthetic transportation support?",
                    "options": [
                        {"value": "yes", "label": "Yes"},
                        {"value": "no", "label": "No"},
                    ],
                    "required": True,
                },
            ],
            "version": f"weekly-synthetic-check-in-v{version}",
        }
        questionnaires[version] = questionnaire
        _insert(
            session,
            "INSERT INTO check_in_definition "
            "(id, organization_id, pathway_definition_id, slug, version, title, questionnaire, created_at) VALUES "
            "(:id, :organization, :pathway, 'weekly-synthetic-check-in', :version, :title, "
            "CAST(:questionnaire AS jsonb), :seed) ON CONFLICT DO NOTHING",
            values
            | {
                "id": ids[definition_key],
                "pathway": ids[pathway_key],
                "version": version,
                "title": f"Weekly synthetic check-in v{version}",
                "questionnaire": json.dumps(questionnaire, sort_keys=True),
            },
        )

    current_questionnaire = questionnaires[2]
    submissions = (
        (
            "submission_v1",
            None,
            TIMES["submission_v1_at"],
            "worse",
            "yes",
            "Synthetic first submission for the public demo.",
        ),
        (
            "submission_v2",
            ids["submission_v1"],
            TIMES["submission_v2_at"],
            "same",
            "yes",
            "Synthetic correction retained as immutable history.",
        ),
    )
    for (
        submission_key,
        predecessor,
        submitted_at,
        pain_change,
        transportation,
        free_text,
    ) in submissions:
        answers = {
            "questionnaire_version": current_questionnaire["version"],
            "questionnaire_canonical": current_questionnaire["canonical"],
            "items": [
                {
                    "link_id": "pain_change",
                    "label": (
                        "Since your last check-in, is synthetic pain better, the same, or worse?"
                    ),
                    "value": pain_change,
                },
                {
                    "link_id": "transportation",
                    "label": "Need synthetic transportation support?",
                    "value": transportation,
                },
            ],
            "free_text": free_text,
            "provenance": {
                "source": "patient-supplied",
                "actor_id": str(ids["patient_user"]),
            },
        }
        _insert(
            session,
            "INSERT INTO check_in_submission "
            "(id, organization_id, patient_id, care_episode_id, check_in_definition_id, status, "
            "answers, submission_source, submitted_by_user_id, supersedes_submission_id, "
            "submitted_at, created_at) VALUES "
            "(:id, :organization, :patient, :episode, :definition, 'submitted', "
            "CAST(:answers AS jsonb), 'patient', :patient_user, :predecessor, :submitted_at, "
            ":submitted_at) ON CONFLICT DO NOTHING",
            values
            | {
                "id": ids[submission_key],
                "definition": ids["definition_v2"],
                "predecessor": predecessor,
                "submitted_at": submitted_at,
                "answers": json.dumps(answers, sort_keys=True),
            },
        )

    _insert(
        session,
        "INSERT INTO reported_need "
        "(id, organization_id, patient_id, care_episode_id, source_submission_id, kind, status, "
        "evidence, created_at) VALUES "
        "(:closed_need, :organization, :patient, :episode, :submission_v1, 'transportation', "
        "'in_progress', CAST(:closed_evidence AS jsonb), :submission_v1_at) ON CONFLICT DO NOTHING",
        values
        | {
            "closed_evidence": json.dumps(
                [{"question_id": "transportation", "value": "true"}], sort_keys=True
            )
        },
    )
    _insert(
        session,
        "INSERT INTO reported_need "
        "(id, organization_id, patient_id, care_episode_id, reopened_from_need_id, kind, status, "
        "evidence, created_at) VALUES "
        "(:open_need, :organization, :patient, :episode, :closed_need, 'transportation', 'open', "
        "CAST(:open_evidence AS jsonb), :need_reopened) ON CONFLICT DO NOTHING",
        values
        | {
            "open_evidence": json.dumps(
                [{"reason": "Synthetic recurrence after prior closure"}], sort_keys=True
            )
        },
    )
    _insert(
        session,
        "INSERT INTO navigation_task "
        "(id, organization_id, patient_id, reported_need_id, assignee_user_id, title, status, "
        "created_at, cancelled_by_user_id, cancelled_at, cancellation_reason) VALUES "
        "(:cancelled_task, :organization, :patient, :closed_need, :navigator_user, "
        "'Arrange synthetic transportation', 'cancelled', :submission_v1_at, :navigator_user, "
        ":need_closed, 'need_closed') ON CONFLICT DO NOTHING",
        values,
    )
    _insert(
        session,
        "INSERT INTO navigation_task "
        "(id, organization_id, patient_id, reported_need_id, title, status, due_at, created_at) VALUES "
        "(:open_task, :organization, :patient, :open_need, 'Review synthetic recurrence', 'open', "
        "'2026-02-12T12:00:00+00:00', :need_reopened) ON CONFLICT DO NOTHING",
        values,
    )
    _insert(
        session,
        "INSERT INTO outcome "
        "(id, organization_id, patient_id, reported_need_id, recorded_by_user_id, disposition, "
        "note, idempotency_key, recorded_at) VALUES "
        "(:outcome, :organization, :patient, :closed_need, :navigator_user, 'resolved', "
        "'Synthetic transportation was arranged.', 'synthetic-closed-need-v1', :need_closed) "
        "ON CONFLICT DO NOTHING",
        values,
    )

    for rule_key, code, kind, name in (
        (
            "deterministic_rule",
            "synthetic-review-required",
            "deterministic",
            "Synthetic review threshold",
        ),
        (
            "human_escalation_rule",
            "synthetic-human-recovery",
            "human_escalation",
            "Synthetic human recovery",
        ),
    ):
        _insert(
            session,
            "INSERT INTO signal_rule "
            "(id, organization_id, rule_code, version, rule_kind, name, created_at) VALUES "
            "(:id, :organization, :code, 1, :kind, :name, :seed) ON CONFLICT DO NOTHING",
            values | {"id": ids[rule_key], "code": code, "kind": kind, "name": name},
        )

    signal_rows = (
        (
            "dismissed_signal",
            ids["submission_v2"],
            None,
            "deterministic_rule",
            "routine",
            "routine",
            "acknowledged",
            ids["navigator_user"],
            TIMES["acknowledged"],
        ),
        (
            "recovery_signal",
            None,
            ids["dismissed_signal"],
            "human_escalation_rule",
            "urgent",
            "urgent",
            "open",
            None,
            None,
        ),
        (
            "acknowledged_signal",
            ids["submission_v2"],
            None,
            "deterministic_rule",
            "routine",
            "urgent",
            "acknowledged",
            ids["navigator_user"],
            TIMES["acknowledged"],
        ),
        (
            "resolved_signal",
            ids["submission_v2"],
            None,
            "deterministic_rule",
            "urgent",
            "urgent",
            "acknowledged",
            ids["navigator_user"],
            TIMES["acknowledged"],
        ),
    )
    for (
        signal_key,
        source_submission,
        predecessor_signal,
        rule_key,
        deterministic_level,
        effective_level,
        status,
        acknowledger,
        acknowledged_at,
    ) in signal_rows:
        _insert(
            session,
            "INSERT INTO safety_signal "
            "(id, organization_id, patient_id, care_episode_id, source_submission_id, "
            "escalated_from_signal_id, signal_rule_id, signal_rule_version, deterministic_level, "
            "effective_level, status, evidence, acknowledged_by_user_id, acknowledged_at, created_at) VALUES "
            "(:id, :organization, :patient, :episode, :source_submission, :predecessor_signal, "
            ":rule_id, 1, :deterministic_level, :effective_level, :status, "
            "CAST(:evidence AS jsonb), :acknowledger, :acknowledged_at, :submission_v2_at) "
            "ON CONFLICT DO NOTHING",
            values
            | {
                "id": ids[signal_key],
                "source_submission": source_submission,
                "predecessor_signal": predecessor_signal,
                "rule_id": ids[rule_key],
                "deterministic_level": deterministic_level,
                "effective_level": effective_level,
                "status": status,
                "evidence": json.dumps(
                    [{"source": "synthetic", "signal": signal_key}], sort_keys=True
                ),
                "acknowledger": acknowledger,
                "acknowledged_at": acknowledged_at,
            },
        )
    _insert(
        session,
        "INSERT INTO safety_signal_resolution "
        "(id, organization_id, safety_signal_id, resolved_by_user_id, resolved_at, resolution_reason) VALUES "
        "(:signal_resolution, :organization, :resolved_signal, :navigator_user, :resolved, "
        "'Synthetic navigator confirmed follow-up.') ON CONFLICT DO NOTHING",
        values,
    )

    policy_rows = (
        ("dismiss_policy", "dismiss_signal", "urgent", True, "navigator"),
        ("override_policy", "override_signal_severity", None, True, "navigator"),
        ("task_policy", "authorize_navigation_task", None, False, "navigator"),
        ("message_policy", "authorize_patient_message", None, True, "navigator"),
    )
    for policy_key, change_type, threshold, self_approval, role in policy_rows:
        _insert(
            session,
            "INSERT INTO approval_policy "
            "(id, organization_id, change_type, version, effective_from, "
            "deterministic_severity_threshold, allow_self_approval, required_approval_count, "
            "required_approver_role, created_at) VALUES "
            "(:id, :organization, :change_type, 1, :seed, :threshold, :self_approval, 1, :role, "
            ":seed) ON CONFLICT DO NOTHING",
            values
            | {
                "id": ids[policy_key],
                "change_type": change_type,
                "threshold": threshold,
                "self_approval": self_approval,
                "role": role,
            },
        )

    _insert(
        session,
        "INSERT INTO resource "
        "(id, organization_id, name, category, url, is_active, metadata, created_at) VALUES "
        "(:resource, :organization, 'Synthetic ride program', 'transportation', "
        "'https://example.test/synthetic-rides', true, CAST(:metadata AS jsonb), :seed) "
        "ON CONFLICT DO NOTHING",
        values | {"metadata": json.dumps({"synthetic": True}, sort_keys=True)},
    )
    _insert(
        session,
        "INSERT INTO patient_message "
        "(id, organization_id, patient_id, body, created_at) VALUES "
        "(:patient_message, :organization, :patient, "
        "'Synthetic reminder: your navigation team has reviewed your check-in.', :proposed) "
        "ON CONFLICT DO NOTHING",
        values,
    )

    _insert(
        session,
        "INSERT INTO workflow_run "
        "(id, organization_id, patient_id, care_episode_id, source_submission_id, trace_id, "
        "initial_state, current_state, started_at, updated_at) VALUES "
        "(:workflow, :organization, :patient, :episode, :submission_v2, 'synthetic-workflow-001', "
        "'received', 'complete', :workflow_started, :transition_complete_at) ON CONFLICT DO NOTHING",
        values,
    )
    transition_rows = (
        (
            "transition_triaged",
            1,
            "received",
            "triaged",
            "system",
            None,
            "synthetic-workflow",
            "1.0",
            TIMES["transition_triaged_at"],
        ),
        (
            "transition_complete",
            2,
            "triaged",
            "complete",
            "user",
            ids["navigator_user"],
            None,
            None,
            TIMES["transition_complete_at"],
        ),
    )
    for (
        transition_key,
        sequence,
        from_state,
        to_state,
        actor_type,
        actor_user,
        system_component,
        system_version,
        transitioned_at,
    ) in transition_rows:
        _insert(
            session,
            "INSERT INTO workflow_transition_event "
            "(id, organization_id, workflow_run_id, sequence_number, from_state, to_state, "
            "actor_type, actor_user_id, actor_system_component, actor_system_version, reason, "
            "transitioned_at) VALUES "
            "(:id, :organization, :workflow, :sequence, :from_state, :to_state, :actor_type, "
            ":actor_user, :system_component, :system_version, :reason, :transitioned_at) "
            "ON CONFLICT DO NOTHING",
            values
            | {
                "id": ids[transition_key],
                "sequence": sequence,
                "from_state": from_state,
                "to_state": to_state,
                "actor_type": actor_type,
                "actor_user": actor_user,
                "system_component": system_component,
                "system_version": system_version,
                "reason": f"Synthetic transition to {to_state}",
                "transitioned_at": transitioned_at,
            },
        )
    for run_key, trace_id, status in (
        ("successful_agent_run", "synthetic-agent-success-001", "succeeded"),
        ("failed_agent_run", "synthetic-agent-failed-001", "failed"),
    ):
        _insert(
            session,
            "INSERT INTO agent_run "
            "(id, organization_id, patient_id, source_submission_id, workflow_run_id, "
            "workflow_transition_event_id, trace_id, agent_name, status, input_payload, "
            "output_payload, validation, created_at, completed_at) VALUES "
            "(:id, :organization, :patient, :submission_v2, :workflow, :transition_complete, "
            ":trace_id, 'synthetic-navigation-agent', :status, CAST(:input AS jsonb), "
            "CAST(:output AS jsonb), CAST(:validation AS jsonb), :agent_run_at, :agent_run_at) "
            "ON CONFLICT DO NOTHING",
            values
            | {
                "id": ids[run_key],
                "trace_id": trace_id,
                "status": status,
                "input": json.dumps({"synthetic": True, "submission": str(ids["submission_v2"])}),
                "output": json.dumps({"status": status, "synthetic": True}),
                "validation": json.dumps({"schema": "valid", "synthetic": True}),
            },
        )
    _insert(
        session,
        "INSERT INTO manual_review_task "
        "(id, organization_id, workflow_run_id, agent_run_id, failure_reason, retry_context, state, "
        "created_at) VALUES "
        "(:manual_review, :organization, :workflow, :failed_agent_run, "
        "'Synthetic tool timeout for recovery demonstration', CAST(:retry_context AS jsonb), "
        "'open', :agent_run_at) ON CONFLICT DO NOTHING",
        values | {"retry_context": json.dumps({"attempt": 1, "synthetic": True})},
    )

    task_value = {
        "title": "Review synthetic recurrence",
        "resources": [
            {
                "resource_id": str(ids["resource"]),
                "name": "Synthetic ride program",
                "category": "transportation",
                "url": "https://example.test/synthetic-rides",
                "metadata": {"synthetic": True},
                "match_rationale": "Matches the synthetic transportation need.",
            }
        ],
    }
    proposal_rows = (
        (
            "dismiss_proposal",
            "dismiss_signal",
            "navigator_user",
            None,
            {"category": "false_positive"},
            "ojcc.dismiss-signal",
            1,
            "dismissed_signal",
            None,
            None,
            "dismiss_policy",
            "urgent",
            True,
            None,
        ),
        (
            "override_proposal",
            "override_signal_severity",
            "navigator_user",
            None,
            {"level": "urgent"},
            "ojcc.override-signal-severity",
            1,
            "acknowledged_signal",
            None,
            None,
            "override_policy",
            None,
            True,
            None,
        ),
        (
            "task_proposal",
            "authorize_navigation_task",
            None,
            "successful_agent_run",
            task_value,
            "ojcc.authorize-navigation-task",
            2,
            None,
            "open_task",
            None,
            "task_policy",
            None,
            False,
            None,
        ),
        (
            "message_proposal",
            "authorize_patient_message",
            "navigator_user",
            None,
            {"body": "Synthetic reminder: your navigation team has reviewed your check-in."},
            "ojcc.authorize-patient-message",
            1,
            None,
            None,
            "patient_message",
            "message_policy",
            None,
            True,
            None,
        ),
        (
            "declined_proposal",
            "authorize_patient_message",
            "navigator_user",
            None,
            {"body": "Synthetic earlier message draft."},
            "ojcc.authorize-patient-message",
            1,
            None,
            None,
            "patient_message",
            "message_policy",
            None,
            True,
            None,
        ),
        (
            "revised_proposal",
            "authorize_patient_message",
            "navigator_user",
            None,
            {"body": "Synthetic revised message awaiting review."},
            "ojcc.authorize-patient-message",
            1,
            None,
            None,
            "patient_message",
            "message_policy",
            None,
            True,
            "declined_proposal",
        ),
    )
    for (
        proposal_key,
        change_type,
        proposer_user_key,
        proposer_agent_key,
        proposed_value,
        schema_id,
        schema_version,
        signal_key,
        task_key,
        message_key,
        policy_key,
        threshold,
        self_approval,
        predecessor_key,
    ) in proposal_rows:
        _insert(
            session,
            "INSERT INTO proposed_change "
            "(id, organization_id, proposed_by_user_id, proposed_by_agent_run_id, proposed_at, "
            "change_type, proposed_value, rationale, value_schema_id, value_schema_version, "
            "supersedes_proposed_change_id, safety_signal_id, navigation_task_id, patient_message_id, "
            "approval_policy_id, approval_policy_version, deterministic_severity_threshold_snapshot, "
            "allow_self_approval_snapshot, required_approval_count_snapshot, "
            "required_approver_role_snapshot) VALUES "
            "(:id, :organization, :proposer_user, :proposer_agent, :proposed, :change_type, "
            "CAST(:proposed_value AS jsonb), :rationale, :schema_id, :schema_version, :predecessor, "
            ":signal_id, :task_id, :message_id, :policy_id, 1, :threshold, :self_approval, 1, "
            "'navigator') ON CONFLICT DO NOTHING",
            values
            | {
                "id": ids[proposal_key],
                "proposer_user": ids[proposer_user_key] if proposer_user_key else None,
                "proposer_agent": ids[proposer_agent_key] if proposer_agent_key else None,
                "change_type": change_type,
                "proposed_value": json.dumps(proposed_value, sort_keys=True),
                "rationale": f"Entirely synthetic {change_type} demonstration.",
                "schema_id": schema_id,
                "schema_version": schema_version,
                "predecessor": ids[predecessor_key] if predecessor_key else None,
                "signal_id": ids[signal_key] if signal_key else None,
                "task_id": ids[task_key] if task_key else None,
                "message_id": ids[message_key] if message_key else None,
                "policy_id": ids[policy_key],
                "threshold": threshold,
                "self_approval": self_approval,
            },
        )

    decision_rows = (
        ("dismiss_decision", "dismiss_proposal", "approved", None),
        ("override_decision", "override_proposal", "approved", None),
        ("task_decision", "task_proposal", "approved", None),
        ("message_decision", "message_proposal", "approved", None),
        (
            "decline_decision",
            "declined_proposal",
            "declined",
            "Synthetic draft was intentionally replaced.",
        ),
    )
    for decision_key, proposal_key, decision, reason in decision_rows:
        _insert(
            session,
            "INSERT INTO approval_decision "
            "(id, organization_id, proposed_change_id, authorized_by_user_id, "
            "qualifying_role_assignment_id, qualifying_role_snapshot, decision, authorized_at, reason) VALUES "
            "(:id, :organization, :proposal_id, :navigator_user, :navigator_role, 'navigator', "
            ":decision, :approved, :reason) ON CONFLICT DO NOTHING",
            values
            | {
                "id": ids[decision_key],
                "proposal_id": ids[proposal_key],
                "decision": decision,
                "reason": reason,
            },
        )

    _insert(
        session,
        "UPDATE safety_signal SET dismissal_proposed_change_id = :dismiss_proposal "
        "WHERE id = :dismissed_signal AND dismissal_proposed_change_id IS NULL",
        values,
    )
    _insert(
        session,
        "UPDATE safety_signal SET current_severity_override_proposed_change_id = :override_proposal "
        "WHERE id = :acknowledged_signal "
        "AND current_severity_override_proposed_change_id IS NULL",
        values,
    )
    _insert(
        session,
        "INSERT INTO navigation_task_resource "
        "(id, organization_id, navigation_task_id, resource_id, proposed_change_id, "
        "resource_name_snapshot, resource_category_snapshot, resource_url_snapshot, "
        "resource_metadata_snapshot, match_rationale_snapshot, proposed_at, approved_at, "
        "delivered_at, delivered_by_user_id) VALUES "
        "(:task_resource, :organization, :open_task, :resource, :task_proposal, "
        "'Synthetic ride program', 'transportation', 'https://example.test/synthetic-rides', "
        "CAST(:resource_metadata AS jsonb), 'Matches the synthetic transportation need.', "
        ":proposed, :approved, :delivered, :navigator_user) ON CONFLICT DO NOTHING",
        values | {"resource_metadata": json.dumps({"synthetic": True}, sort_keys=True)},
    )

    _insert(
        session,
        "INSERT INTO knowledge_document "
        "(id, organization_id, resource_id, title, version, content, citations, created_at) VALUES "
        "(:knowledge_document, :organization, :resource, 'Synthetic transportation guide', '1.0', "
        "'Synthetic-only guidance for demonstrating governed retrieval.', "
        "CAST(:citations AS jsonb), :seed) ON CONFLICT DO NOTHING",
        values | {"citations": json.dumps([{"label": "Synthetic source", "url": "https://example.test"}])},
    )
    _insert(
        session,
        "INSERT INTO organization_knowledge_approval "
        "(id, organization_id, knowledge_document_id, knowledge_document_version, "
        "approved_by_user_id, approved_by_role_assignment_id, approved_at, effective_from, created_at) VALUES "
        "(:knowledge_approval, :organization, :knowledge_document, '1.0', :administrator_user, "
        ":administrator_role, :seed, :pathway_change, :seed) ON CONFLICT DO NOTHING",
        values,
    )
    _insert(
        session,
        "INSERT INTO agent_run_citation "
        "(id, organization_id, agent_run_id, knowledge_document_id, knowledge_document_version, "
        "passage, cited_at) VALUES "
        "(:citation, :organization, :successful_agent_run, :knowledge_document, '1.0', "
        "'Synthetic transportation services require navigator confirmation.', :agent_run_at) "
        "ON CONFLICT DO NOTHING",
        values,
    )

    audit_rows = (
        (
            "audit_user",
            "user",
            ids["navigator_user"],
            None,
            None,
            None,
            None,
            None,
            "navigation_task",
            ids["cancelled_task"],
            "task_cancelled_by_closure",
            {
                "outcome_id": str(ids["outcome"]),
                "cancellation_reason": "need_closed",
            },
            TIMES["need_closed"],
        ),
        (
            "audit_agent",
            "agent",
            None,
            ids["successful_agent_run"],
            None,
            None,
            None,
            None,
            "agent_run",
            ids["successful_agent_run"],
            "synthetic_agent_completed",
            {"synthetic": True},
            TIMES["agent_run_at"],
        ),
        (
            "audit_policy",
            "policy",
            None,
            None,
            "synthetic-safety-policy",
            "1.0",
            None,
            None,
            "safety_signal",
            ids["dismissed_signal"],
            "synthetic_policy_evaluated",
            {"synthetic": True},
            TIMES["approved"],
        ),
        (
            "audit_system",
            "system",
            None,
            None,
            None,
            None,
            "synthetic-seed",
            "1.0",
            "organization",
            ids["organization"],
            "synthetic_demo_seeded",
            {"synthetic": True},
            SEED_TIME,
        ),
    )
    for (
        audit_key,
        actor_type,
        actor_user,
        actor_agent,
        policy_component,
        policy_version,
        system_component,
        system_version,
        entity_type,
        entity_id,
        event_type,
        payload,
        created_at,
    ) in audit_rows:
        _insert(
            session,
            "INSERT INTO audit_event "
            "(id, organization_id, actor_type, actor_user_id, actor_agent_run_id, "
            "actor_policy_component, actor_policy_version, actor_system_component, "
            "actor_system_version, entity_type, entity_id, event_type, payload, created_at) VALUES "
            "(:id, :organization, :actor_type, :actor_user, :actor_agent, :policy_component, "
            ":policy_version, :system_component, :system_version, :entity_type, :entity_id, "
            ":event_type, CAST(:payload AS jsonb), :created_at) ON CONFLICT DO NOTHING",
            values
            | {
                "id": ids[audit_key],
                "actor_type": actor_type,
                "actor_user": actor_user,
                "actor_agent": actor_agent,
                "policy_component": policy_component,
                "policy_version": policy_version,
                "system_component": system_component,
                "system_version": system_version,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "event_type": event_type,
                "payload": json.dumps(payload, sort_keys=True),
                "created_at": created_at,
            },
        )

    session.execute(text("SET LOCAL session_replication_role = origin"))
    return SeedSummary(organization_id=ids["organization"], row_counts=_row_counts(session))


def validate_disposable_database_url(database_url: str) -> URL:
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql" or url.host not in LOOPBACK_HOSTS:
        raise ValueError("Synthetic seed requires a loopback PostgreSQL URL")
    if url.query:
        raise ValueError("Synthetic seed URL must not include query parameters")
    if url.port != 5432:
        raise ValueError("Synthetic seed requires the explicit local PostgreSQL port 5432")
    if url.database is None or DISPOSABLE_DATABASE_PATTERN.fullmatch(url.database) is None:
        raise ValueError(
            "Refusing to seed a non-disposable database; expected "
            "ojcc_demo_<8-32 lowercase hex> or ojcc_task7_<8-32 lowercase hex>"
        )
    return url


@contextmanager
def without_libpq_environment() -> Iterator[None]:
    """Make the explicit validated URL the only libpq connection route."""
    inherited = {
        key: value for key, value in os.environ.items() if key.upper().startswith("PG")
    }
    try:
        for key in inherited:
            os.environ.pop(key, None)
        yield
    finally:
        for key in tuple(os.environ):
            if key.upper().startswith("PG"):
                os.environ.pop(key, None)
        os.environ.update(inherited)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed the deterministic synthetic public demo.")
    parser.add_argument("--database-url", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    with without_libpq_environment():
        validated_url = validate_disposable_database_url(arguments.database_url)
        engine = create_engine(validated_url, pool_pre_ping=True)
        try:
            with Session(engine) as session, session.begin():
                summary = seed_demo(session)
                violations = inspect_integrity(session)
                if violations:
                    raise RuntimeError(
                        "Synthetic seed failed integrity: "
                        + json.dumps(
                            [violation.as_dict() for violation in violations], sort_keys=True
                        )
                    )
        finally:
            engine.dispose()
    print(json.dumps({"status": "seeded", **summary.as_dict()}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
