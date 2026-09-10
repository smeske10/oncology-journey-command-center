from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class IntegrityViolation:
    category: str
    identifiers: dict[str, str]
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "identifiers": self.identifiers,
            "evidence": self.evidence,
        }


def _identifier(value: Any) -> str:
    return str(value)


def _evidence_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_evidence_value(item) for item in value]
    return value


def _add_rows(
    session: Session,
    violations: list[IntegrityViolation],
    *,
    category: str,
    query: str,
    identifier_columns: tuple[str, ...],
    evidence_columns: tuple[str, ...],
) -> None:
    for row in session.execute(text(query)).mappings():
        violations.append(
            IntegrityViolation(
                category=category,
                identifiers={column: _identifier(row[column]) for column in identifier_columns},
                evidence={column: _evidence_value(row[column]) for column in evidence_columns},
            )
        )


def inspect_integrity(session: Session) -> list[IntegrityViolation]:
    """Inspect restored domain data without changing or reconciling any record."""
    violations: list[IntegrityViolation] = []

    _add_rows(
        session,
        violations,
        category="closed_need_non_terminal_task",
        query="""
            SELECT
                need.id AS reported_need_id,
                task.id AS navigation_task_id,
                outcome.id AS outcome_id,
                task.status::text AS task_status
            FROM outcome
            JOIN reported_need AS need
              ON need.organization_id = outcome.organization_id
             AND need.patient_id = outcome.patient_id
             AND need.id = outcome.reported_need_id
            JOIN navigation_task AS task
              ON task.organization_id = need.organization_id
             AND task.patient_id = need.patient_id
             AND task.reported_need_id = need.id
            WHERE task.status IN ('open', 'assigned', 'in_progress')
            ORDER BY need.id, task.id
        """,
        identifier_columns=("reported_need_id", "navigation_task_id", "outcome_id"),
        evidence_columns=("task_status",),
    )

    _add_rows(
        session,
        violations,
        category="task_after_need_closure",
        query="""
            WITH task_events AS (
                SELECT
                    need.id AS reported_need_id,
                    task.id AS navigation_task_id,
                    outcome.id AS outcome_id,
                    outcome.recorded_at AS need_closed_at,
                    task.created_at AS task_event_at,
                    task.status::text AS task_status,
                    'created'::text AS event
                FROM outcome
                JOIN reported_need AS need
                  ON need.organization_id = outcome.organization_id
                 AND need.patient_id = outcome.patient_id
                 AND need.id = outcome.reported_need_id
                JOIN navigation_task AS task
                  ON task.organization_id = need.organization_id
                 AND task.patient_id = need.patient_id
                 AND task.reported_need_id = need.id
                WHERE task.created_at > outcome.recorded_at
                UNION ALL
                SELECT
                    need.id, task.id, outcome.id, outcome.recorded_at, task.completed_at,
                    task.status::text, 'completed'::text
                FROM outcome
                JOIN reported_need AS need
                  ON need.organization_id = outcome.organization_id
                 AND need.patient_id = outcome.patient_id
                 AND need.id = outcome.reported_need_id
                JOIN navigation_task AS task
                  ON task.organization_id = need.organization_id
                 AND task.patient_id = need.patient_id
                 AND task.reported_need_id = need.id
                WHERE task.completed_at > outcome.recorded_at
                UNION ALL
                SELECT
                    need.id, task.id, outcome.id, outcome.recorded_at, task.cancelled_at,
                    task.status::text, 'cancelled'::text
                FROM outcome
                JOIN reported_need AS need
                  ON need.organization_id = outcome.organization_id
                 AND need.patient_id = outcome.patient_id
                 AND need.id = outcome.reported_need_id
                JOIN navigation_task AS task
                  ON task.organization_id = need.organization_id
                 AND task.patient_id = need.patient_id
                 AND task.reported_need_id = need.id
                WHERE task.cancelled_at > outcome.recorded_at
            )
            SELECT * FROM task_events
            ORDER BY reported_need_id, navigation_task_id, event
        """,
        identifier_columns=("reported_need_id", "navigation_task_id", "outcome_id"),
        evidence_columns=("event", "need_closed_at", "task_event_at", "task_status"),
    )

    _add_rows(
        session,
        violations,
        category="missing_task_cancellation_audit_event",
        query="""
            SELECT
                need.id AS reported_need_id,
                task.id AS navigation_task_id,
                outcome.id AS outcome_id,
                CASE
                    WHEN NOT EXISTS (
                        SELECT 1
                        FROM audit_event AS candidate
                        WHERE candidate.organization_id = outcome.organization_id
                          AND candidate.entity_type = 'navigation_task'
                          AND candidate.entity_id = task.id
                          AND candidate.event_type = 'task_cancelled_by_closure'
                    ) THEN 'missing_event'
                    ELSE 'incorrect_attribution'
                END AS issue,
                outcome.recorded_by_user_id AS expected_actor_user_id,
                outcome.recorded_at AS expected_created_at
            FROM outcome
            JOIN reported_need AS need
              ON need.organization_id = outcome.organization_id
             AND need.patient_id = outcome.patient_id
             AND need.id = outcome.reported_need_id
            JOIN navigation_task AS task
              ON task.organization_id = need.organization_id
             AND task.patient_id = need.patient_id
             AND task.reported_need_id = need.id
             AND task.status = 'cancelled'
             AND task.cancellation_reason = 'need_closed'
            WHERE task.cancelled_by_user_id IS DISTINCT FROM outcome.recorded_by_user_id
               OR task.cancelled_at IS DISTINCT FROM outcome.recorded_at
               OR NOT EXISTS (
                    SELECT 1
                    FROM audit_event AS event
                    WHERE event.organization_id = outcome.organization_id
                      AND event.entity_type = 'navigation_task'
                      AND event.entity_id = task.id
                      AND event.event_type = 'task_cancelled_by_closure'
                      AND event.actor_type = 'user'
                      AND event.actor_user_id = outcome.recorded_by_user_id
                      AND event.created_at = outcome.recorded_at
                      AND event.payload->>'outcome_id' = outcome.id::text
                      AND event.payload->>'cancellation_reason' = 'need_closed'
               )
            ORDER BY need.id, task.id
        """,
        identifier_columns=("reported_need_id", "navigation_task_id", "outcome_id"),
        evidence_columns=("issue", "expected_actor_user_id", "expected_created_at"),
    )

    _add_rows(
        session,
        violations,
        category="signal_dual_terminal_paths",
        query="""
            SELECT
                signal.id AS safety_signal_id,
                resolution.id AS safety_signal_resolution_id,
                signal.dismissal_proposed_change_id,
                coalesce(proposal.effective_state::text, 'missing') AS dismissal_state
            FROM safety_signal AS signal
            JOIN safety_signal_resolution AS resolution
              ON resolution.organization_id = signal.organization_id
             AND resolution.safety_signal_id = signal.id
            LEFT JOIN effective_proposed_change_state AS proposal
              ON proposal.organization_id = signal.organization_id
             AND proposal.id = signal.dismissal_proposed_change_id
            WHERE signal.dismissal_proposed_change_id IS NOT NULL
            ORDER BY signal.id
        """,
        identifier_columns=(
            "safety_signal_id",
            "safety_signal_resolution_id",
            "dismissal_proposed_change_id",
        ),
        evidence_columns=("dismissal_state",),
    )

    _add_rows(
        session,
        violations,
        category="applied_proposal_without_qualifying_approvals",
        query="""
            WITH applications AS (
                SELECT
                    signal.organization_id,
                    signal.dismissal_proposed_change_id AS proposed_change_id,
                    signal.id AS target_id,
                    'signal_dismissal'::text AS application
                FROM safety_signal AS signal
                WHERE signal.dismissal_proposed_change_id IS NOT NULL
                UNION
                SELECT
                    signal.organization_id,
                    signal.current_severity_override_proposed_change_id,
                    signal.id,
                    'signal_severity_override'::text
                FROM safety_signal AS signal
                WHERE signal.current_severity_override_proposed_change_id IS NOT NULL
                UNION
                SELECT
                    resource.organization_id,
                    resource.proposed_change_id,
                    resource.navigation_task_id,
                    'navigation_task_resource_approval'::text
                FROM navigation_task_resource AS resource
                WHERE resource.approved_at IS NOT NULL
            ), proposal_facts AS (
                SELECT
                    application.*,
                    proposal.change_type::text,
                    proposal.organization_id AS proposal_organization_id,
                    proposal.safety_signal_id,
                    proposal.navigation_task_id,
                    proposal.patient_message_id,
                    state.effective_state::text,
                    CASE
                        WHEN proposal.change_type = 'dismiss_signal'
                         AND (
                            proposal.deterministic_severity_threshold_snapshot IS NULL
                            OR safety_severity_rank(signal.deterministic_level) >=
                               safety_severity_rank(
                                   proposal.deterministic_severity_threshold_snapshot
                               )
                         )
                        THEN greatest(proposal.required_approval_count_snapshot, 2)
                        ELSE proposal.required_approval_count_snapshot
                    END AS required_approval_count,
                    (
                        SELECT count(DISTINCT decision.authorized_by_user_id)::integer
                        FROM approval_decision AS decision
                        JOIN role_assignment AS assignment
                          ON assignment.organization_id = decision.organization_id
                         AND assignment.user_id = decision.authorized_by_user_id
                         AND assignment.id = decision.qualifying_role_assignment_id
                         AND assignment.role = proposal.required_approver_role_snapshot
                         AND decision.qualifying_role_snapshot =
                             proposal.required_approver_role_snapshot
                         AND decision.authorized_at >= assignment.granted_at
                         AND (
                            assignment.revoked_at IS NULL
                            OR decision.authorized_at < assignment.revoked_at
                         )
                        WHERE decision.organization_id = proposal.organization_id
                          AND decision.proposed_change_id = proposal.id
                          AND decision.decision = 'approved'
                          AND (
                            proposal.proposed_by_user_id IS NULL
                            OR decision.authorized_by_user_id <> proposal.proposed_by_user_id
                            OR (
                                proposal.allow_self_approval_snapshot
                                AND NOT (
                                    proposal.change_type = 'dismiss_signal'
                                    AND (
                                        proposal.deterministic_severity_threshold_snapshot IS NULL
                                        OR safety_severity_rank(signal.deterministic_level) >=
                                           safety_severity_rank(
                                               proposal.deterministic_severity_threshold_snapshot
                                           )
                                    )
                                )
                            )
                          )
                    ) AS qualifying_approval_count,
                    EXISTS (
                        SELECT 1 FROM proposed_change AS successor
                        WHERE successor.organization_id = proposal.organization_id
                          AND successor.supersedes_proposed_change_id = proposal.id
                    ) AS has_successor
                FROM applications AS application
                LEFT JOIN proposed_change AS proposal
                  ON proposal.id = application.proposed_change_id
                LEFT JOIN effective_proposed_change_state AS state
                  ON state.id = proposal.id
                 AND state.organization_id = proposal.organization_id
                LEFT JOIN safety_signal AS signal
                  ON signal.id = proposal.safety_signal_id
                 AND signal.organization_id = proposal.organization_id
            )
            SELECT
                proposed_change_id,
                target_id,
                application,
                coalesce(effective_state, 'missing') AS effective_state,
                coalesce(qualifying_approval_count, 0) AS qualifying_approval_count,
                coalesce(required_approval_count, 0) AS required_approval_count
            FROM proposal_facts
            WHERE effective_state IS DISTINCT FROM 'approved'
               OR has_successor
               OR proposal_organization_id IS DISTINCT FROM organization_id
               OR CASE application
                    WHEN 'signal_dismissal' THEN
                        change_type IS DISTINCT FROM 'dismiss_signal'
                        OR safety_signal_id IS DISTINCT FROM target_id
                    WHEN 'signal_severity_override' THEN
                        change_type IS DISTINCT FROM 'override_signal_severity'
                        OR safety_signal_id IS DISTINCT FROM target_id
                    WHEN 'navigation_task_resource_approval' THEN
                        change_type IS DISTINCT FROM 'authorize_navigation_task'
                        OR navigation_task_id IS DISTINCT FROM target_id
                    ELSE true
                  END
            ORDER BY proposed_change_id, target_id, application
        """,
        identifier_columns=("proposed_change_id", "target_id"),
        evidence_columns=(
            "application",
            "effective_state",
            "qualifying_approval_count",
            "required_approval_count",
        ),
    )

    _add_rows(
        session,
        violations,
        category="cross_organization_approval",
        query="""
            SELECT
                proposal.id AS proposed_change_id,
                decision.id AS approval_decision_id,
                decision.qualifying_role_assignment_id,
                proposal.organization_id AS proposal_organization_id,
                decision.organization_id AS decision_organization_id,
                assignment.organization_id AS role_assignment_organization_id
            FROM approval_decision AS decision
            JOIN proposed_change AS proposal ON proposal.id = decision.proposed_change_id
            LEFT JOIN role_assignment AS assignment
              ON assignment.id = decision.qualifying_role_assignment_id
            WHERE proposal.organization_id IS DISTINCT FROM decision.organization_id
               OR assignment.organization_id IS DISTINCT FROM proposal.organization_id
               OR assignment.organization_id IS DISTINCT FROM decision.organization_id
            ORDER BY proposal.id, decision.id
        """,
        identifier_columns=(
            "proposed_change_id",
            "approval_decision_id",
            "qualifying_role_assignment_id",
        ),
        evidence_columns=(
            "proposal_organization_id",
            "decision_organization_id",
            "role_assignment_organization_id",
        ),
    )

    _add_rows(
        session,
        violations,
        category="invalid_approval_qualification",
        query="""
            SELECT
                proposal.id AS proposed_change_id,
                decision.id AS approval_decision_id,
                decision.qualifying_role_assignment_id,
                decision.authorized_by_user_id,
                assignment.user_id AS role_assignment_user_id,
                assignment.role::text AS role_assignment_role,
                proposal.required_approver_role_snapshot::text AS required_role,
                assignment.granted_at,
                assignment.revoked_at,
                decision.authorized_at
            FROM approval_decision AS decision
            JOIN proposed_change AS proposal
              ON proposal.organization_id = decision.organization_id
             AND proposal.id = decision.proposed_change_id
            LEFT JOIN role_assignment AS assignment
              ON assignment.organization_id = decision.organization_id
             AND assignment.id = decision.qualifying_role_assignment_id
            WHERE assignment.id IS NULL
               OR assignment.user_id IS DISTINCT FROM decision.authorized_by_user_id
               OR assignment.role IS DISTINCT FROM proposal.required_approver_role_snapshot
               OR decision.qualifying_role_snapshot IS DISTINCT FROM
                    proposal.required_approver_role_snapshot
               OR decision.authorized_at < assignment.granted_at
               OR (
                    assignment.revoked_at IS NOT NULL
                    AND decision.authorized_at >= assignment.revoked_at
               )
            ORDER BY proposal.id, decision.id
        """,
        identifier_columns=(
            "proposed_change_id",
            "approval_decision_id",
            "qualifying_role_assignment_id",
        ),
        evidence_columns=(
            "authorized_by_user_id",
            "role_assignment_user_id",
            "role_assignment_role",
            "required_role",
            "granted_at",
            "revoked_at",
            "authorized_at",
        ),
    )

    _add_rows(
        session,
        violations,
        category="invalid_audit_actor",
        query="""
            SELECT
                id AS audit_event_id,
                actor_type::text,
                actor_user_id,
                actor_agent_run_id,
                actor_policy_component,
                actor_policy_version,
                actor_system_component,
                actor_system_version
            FROM audit_event
            WHERE NOT (
                CASE actor_type
                    WHEN 'user' THEN
                        actor_user_id IS NOT NULL AND actor_agent_run_id IS NULL
                        AND actor_policy_component IS NULL AND actor_policy_version IS NULL
                        AND actor_system_component IS NULL AND actor_system_version IS NULL
                    WHEN 'agent' THEN
                        actor_user_id IS NULL AND actor_agent_run_id IS NOT NULL
                        AND actor_policy_component IS NULL AND actor_policy_version IS NULL
                        AND actor_system_component IS NULL AND actor_system_version IS NULL
                    WHEN 'policy' THEN
                        actor_user_id IS NULL AND actor_agent_run_id IS NULL
                        AND NULLIF(trim(actor_policy_component), '') IS NOT NULL
                        AND NULLIF(trim(actor_policy_version), '') IS NOT NULL
                        AND actor_system_component IS NULL AND actor_system_version IS NULL
                    WHEN 'system' THEN
                        actor_user_id IS NULL AND actor_agent_run_id IS NULL
                        AND actor_policy_component IS NULL AND actor_policy_version IS NULL
                        AND NULLIF(trim(actor_system_component), '') IS NOT NULL
                        AND NULLIF(trim(actor_system_version), '') IS NOT NULL
                    ELSE false
                END
            )
            ORDER BY id
        """,
        identifier_columns=("audit_event_id",),
        evidence_columns=(
            "actor_type",
            "actor_user_id",
            "actor_agent_run_id",
            "actor_policy_component",
            "actor_policy_version",
            "actor_system_component",
            "actor_system_version",
        ),
    )

    _add_rows(
        session,
        violations,
        category="overlapping_pathway_assignments",
        query="""
            SELECT
                first.care_episode_id,
                first.id AS first_assignment_id,
                second.id AS second_assignment_id,
                ARRAY[first.effective_from, first.effective_to] AS first_interval,
                ARRAY[second.effective_from, second.effective_to] AS second_interval
            FROM episode_pathway_assignment AS first
            JOIN episode_pathway_assignment AS second
              ON second.organization_id = first.organization_id
             AND second.care_episode_id = first.care_episode_id
             AND second.id > first.id
             AND tstzrange(first.effective_from, first.effective_to, '[)')
                 && tstzrange(second.effective_from, second.effective_to, '[)')
            ORDER BY first.care_episode_id, first.id, second.id
        """,
        identifier_columns=("care_episode_id", "first_assignment_id", "second_assignment_id"),
        evidence_columns=("first_interval", "second_interval"),
    )

    _add_rows(
        session,
        violations,
        category="forked_successor_chain",
        query="""
            WITH forks AS (
                SELECT
                    supersedes_submission_id AS predecessor_id,
                    'submission_correction'::text AS chain_type,
                    array_agg(id ORDER BY id) AS successor_ids
                FROM check_in_submission
                WHERE supersedes_submission_id IS NOT NULL
                GROUP BY supersedes_submission_id
                HAVING count(*) > 1
                UNION ALL
                SELECT
                    reopened_from_need_id,
                    'need_reopening'::text,
                    array_agg(id ORDER BY id)
                FROM reported_need
                WHERE reopened_from_need_id IS NOT NULL
                GROUP BY reopened_from_need_id
                HAVING count(*) > 1
                UNION ALL
                SELECT
                    escalated_from_signal_id,
                    'signal_escalation'::text,
                    array_agg(id ORDER BY id)
                FROM safety_signal
                WHERE escalated_from_signal_id IS NOT NULL
                GROUP BY escalated_from_signal_id
                HAVING count(*) > 1
                UNION ALL
                SELECT
                    supersedes_proposed_change_id,
                    'proposal_revision'::text,
                    array_agg(id ORDER BY id)
                FROM proposed_change
                WHERE supersedes_proposed_change_id IS NOT NULL
                GROUP BY supersedes_proposed_change_id
                HAVING count(*) > 1
            )
            SELECT predecessor_id, chain_type, successor_ids
            FROM forks
            ORDER BY predecessor_id, chain_type
        """,
        identifier_columns=("predecessor_id",),
        evidence_columns=("chain_type", "successor_ids"),
    )

    _add_rows(
        session,
        violations,
        category="invalid_bound_task_authorization",
        query="""
            SELECT
                task.id AS navigation_task_id,
                task.authorized_proposed_change_id,
                CASE
                    WHEN proposal.id IS NULL THEN 'missing_proposal'
                    WHEN proposal.organization_id IS DISTINCT FROM task.organization_id
                      OR proposal.navigation_task_id IS DISTINCT FROM task.id
                        THEN 'wrong_scope_or_target'
                    WHEN proposal.change_type IS DISTINCT FROM 'authorize_navigation_task'
                      OR proposal.value_schema_id IS DISTINCT FROM
                            'ojcc.authorize-navigation-task'
                      OR proposal.value_schema_version NOT IN (1, 2)
                        THEN 'unsupported_contract'
                    WHEN state.effective_state IS DISTINCT FROM 'approved'
                        THEN 'proposal_not_approved'
                    ELSE 'approved_title_mismatch'
                END AS issue,
                coalesce(state.effective_state::text, 'missing') AS effective_state,
                proposal.value_schema_version
            FROM navigation_task AS task
            LEFT JOIN proposed_change AS proposal
              ON proposal.organization_id = task.organization_id
             AND proposal.id = task.authorized_proposed_change_id
            LEFT JOIN effective_proposed_change_state AS state
              ON state.organization_id = proposal.organization_id
             AND state.id = proposal.id
            WHERE task.authorized_proposed_change_id IS NOT NULL
              AND (
                    proposal.id IS NULL
                 OR proposal.organization_id IS DISTINCT FROM task.organization_id
                 OR proposal.navigation_task_id IS DISTINCT FROM task.id
                 OR proposal.change_type IS DISTINCT FROM 'authorize_navigation_task'
                 OR proposal.value_schema_id IS DISTINCT FROM
                        'ojcc.authorize-navigation-task'
                 OR proposal.value_schema_version NOT IN (1, 2)
                 OR state.effective_state IS DISTINCT FROM 'approved'
                 OR task.title IS DISTINCT FROM proposal.proposed_value->>'title'
              )
            ORDER BY task.id
        """,
        identifier_columns=("navigation_task_id", "authorized_proposed_change_id"),
        evidence_columns=("issue", "effective_state", "value_schema_version"),
    )

    _add_rows(
        session,
        violations,
        category="inconsistent_bound_task_lifecycle",
        query="""
            SELECT
                task.id AS navigation_task_id,
                task.authorized_proposed_change_id,
                task.status::text AS task_status,
                task.assignee_user_id,
                task.due_at,
                task.completed_at
            FROM navigation_task AS task
            WHERE task.authorized_proposed_change_id IS NOT NULL
              AND (
                    task.status = 'open'
                 OR task.assignee_user_id IS NULL
                 OR task.due_at IS NULL
                 OR (task.status = 'completed') IS DISTINCT FROM
                    (task.completed_at IS NOT NULL)
              )
            ORDER BY task.id
        """,
        identifier_columns=("navigation_task_id", "authorized_proposed_change_id"),
        evidence_columns=(
            "task_status",
            "assignee_user_id",
            "due_at",
            "completed_at",
        ),
    )

    _add_rows(
        session,
        violations,
        category="invalid_follow_up_request",
        query="""
            WITH request_facts AS (
                SELECT
                    task.id AS navigation_task_id,
                    request.id AS follow_up_request_id,
                    task.authorized_proposed_change_id,
                    task.status::text AS task_status,
                    task.organization_id AS task_organization_id,
                    task.patient_id AS task_patient_id,
                    task.reported_need_id AS task_need_id,
                    task.assignee_user_id,
                    task.completed_at,
                    request.organization_id AS request_organization_id,
                    request.patient_id AS request_patient_id,
                    request.care_episode_id AS request_episode_id,
                    request.reported_need_id AS request_need_id,
                    request.navigation_task_id AS request_task_id,
                    request.requested_by_user_id,
                    request.requested_at,
                    request.prompt_version,
                    need.care_episode_id AS need_episode_id,
                    count(request.id) OVER (
                        PARTITION BY task.organization_id, task.id
                    ) AS request_count
                FROM navigation_task AS task
                FULL OUTER JOIN follow_up_request AS request
                  ON request.organization_id = task.organization_id
                 AND request.navigation_task_id = task.id
                LEFT JOIN reported_need AS need
                  ON need.organization_id = coalesce(
                        task.organization_id, request.organization_id
                     )
                 AND need.id = coalesce(task.reported_need_id, request.reported_need_id)
            )
            SELECT
                navigation_task_id,
                follow_up_request_id,
                CASE
                    WHEN navigation_task_id IS NULL THEN 'missing_task'
                    WHEN authorized_proposed_change_id IS NULL
                      OR task_status IS DISTINCT FROM 'completed'
                        THEN 'task_not_completed_and_bound'
                    WHEN request_count <> 1 THEN 'request_count'
                    ELSE 'scope_or_provenance_mismatch'
                END AS issue,
                request_count,
                task_status,
                request_episode_id,
                need_episode_id
            FROM request_facts
            WHERE (
                authorized_proposed_change_id IS NOT NULL
                AND task_status = 'completed'
                AND (
                    request_count <> 1
                    OR follow_up_request_id IS NULL
                    OR request_organization_id IS DISTINCT FROM task_organization_id
                    OR request_patient_id IS DISTINCT FROM task_patient_id
                    OR request_need_id IS DISTINCT FROM task_need_id
                    OR request_task_id IS DISTINCT FROM navigation_task_id
                    OR request_episode_id IS DISTINCT FROM need_episode_id
                    OR requested_by_user_id IS DISTINCT FROM assignee_user_id
                    OR requested_at IS DISTINCT FROM completed_at
                    OR prompt_version <> 1
                )
            ) OR (
                follow_up_request_id IS NOT NULL
                AND (
                    navigation_task_id IS NULL
                    OR authorized_proposed_change_id IS NULL
                    OR task_status IS DISTINCT FROM 'completed'
                    OR request_organization_id IS DISTINCT FROM task_organization_id
                    OR request_patient_id IS DISTINCT FROM task_patient_id
                    OR request_need_id IS DISTINCT FROM task_need_id
                    OR request_episode_id IS DISTINCT FROM need_episode_id
                    OR requested_by_user_id IS DISTINCT FROM assignee_user_id
                    OR requested_at IS DISTINCT FROM completed_at
                    OR prompt_version <> 1
                )
            )
            ORDER BY navigation_task_id, follow_up_request_id
        """,
        identifier_columns=("navigation_task_id", "follow_up_request_id"),
        evidence_columns=(
            "issue",
            "request_count",
            "task_status",
            "request_episode_id",
            "need_episode_id",
        ),
    )

    _add_rows(
        session,
        violations,
        category="invalid_follow_up_response",
        query="""
            WITH response_facts AS (
                SELECT
                    response.*,
                    request.patient_id AS request_patient_id,
                    request.requested_at,
                    link.patient_id AS linked_patient_id,
                    link.linked_at,
                    link.revoked_at,
                    count(response.id) OVER (
                        PARTITION BY response.organization_id,
                                     response.follow_up_request_id
                    ) AS response_count,
                    EXISTS (
                        SELECT 1
                        FROM role_assignment AS assignment
                        WHERE assignment.organization_id = response.organization_id
                          AND assignment.user_id = response.submitted_by_user_id
                          AND assignment.role = 'supporting_actor'
                          AND assignment.granted_at <= response.submitted_at
                          AND (
                            assignment.revoked_at IS NULL
                            OR response.submitted_at < assignment.revoked_at
                          )
                    ) AS had_supporting_actor_authority
                FROM follow_up_response AS response
                LEFT JOIN follow_up_request AS request
                  ON request.organization_id = response.organization_id
                 AND request.id = response.follow_up_request_id
                LEFT JOIN patient_identity_link AS link
                  ON link.organization_id = response.organization_id
                 AND link.user_id = response.submitted_by_user_id
                 AND link.id = response.patient_identity_link_id
            )
            SELECT
                id AS follow_up_response_id,
                follow_up_request_id,
                submitted_by_user_id,
                patient_identity_link_id,
                CASE
                    WHEN requested_at IS NULL THEN 'missing_request'
                    WHEN response_count <> 1 THEN 'response_count'
                    WHEN submitted_at < requested_at THEN 'submitted_before_request'
                    ELSE 'invalid_patient_attribution'
                END AS issue,
                response_count,
                requested_at,
                submitted_at
            FROM response_facts
            WHERE requested_at IS NULL
               OR response_count <> 1
               OR submitted_at < requested_at
               OR linked_patient_id IS DISTINCT FROM request_patient_id
               OR linked_at > submitted_at
               OR (revoked_at IS NOT NULL AND submitted_at >= revoked_at)
               OR NOT had_supporting_actor_authority
            ORDER BY id
        """,
        identifier_columns=(
            "follow_up_response_id",
            "follow_up_request_id",
            "submitted_by_user_id",
            "patient_identity_link_id",
        ),
        evidence_columns=(
            "issue",
            "response_count",
            "requested_at",
            "submitted_at",
        ),
    )

    _add_rows(
        session,
        violations,
        category="invalid_navigation_task_transition_audit",
        query="""
            WITH expected AS (
                SELECT
                    task.*,
                    transition.event_type,
                    transition.from_status,
                    transition.to_status
                FROM navigation_task AS task
                CROSS JOIN LATERAL (
                    VALUES
                        ('navigation_task_claimed', 'open', 'assigned'),
                        ('navigation_task_started', 'assigned', 'in_progress'),
                        ('navigation_task_completed', 'in_progress', 'completed')
                ) AS transition(event_type, from_status, to_status)
                WHERE task.authorized_proposed_change_id IS NOT NULL
                  AND (
                    transition.event_type = 'navigation_task_claimed'
                    OR (
                        transition.event_type = 'navigation_task_started'
                        AND task.status IN ('in_progress', 'completed')
                    )
                    OR (
                        transition.event_type = 'navigation_task_completed'
                        AND task.status = 'completed'
                    )
                  )
            ), checked AS (
                SELECT
                    expected.id AS navigation_task_id,
                    expected.event_type,
                    array_agg(event.id ORDER BY event.id)
                        FILTER (WHERE event.id IS NOT NULL) AS audit_event_ids,
                    count(event.id) AS event_count,
                    bool_and(
                        event.actor_type = 'user'
                        AND event.actor_user_id = expected.assignee_user_id
                        AND event.entity_type = 'navigation_task'
                        AND event.entity_id = expected.id
                        AND event.payload->>'need_id' = expected.reported_need_id::text
                        AND event.payload->>'authorized_proposed_change_id' =
                            expected.authorized_proposed_change_id::text
                        AND event.payload->>'from_status' = expected.from_status
                        AND event.payload->>'to_status' = expected.to_status
                        AND event.payload->>'assignee_user_id' =
                            expected.assignee_user_id::text
                        AND event.payload->'due_at' = to_jsonb(expected.due_at)
                        AND (
                            expected.event_type <> 'navigation_task_completed'
                            OR (
                                event.created_at = expected.completed_at
                                AND event.payload->>'follow_up_request_id' =
                                    request.id::text
                            )
                        )
                    ) FILTER (WHERE event.id IS NOT NULL) AS consistent
                FROM expected
                LEFT JOIN audit_event AS event
                  ON event.organization_id = expected.organization_id
                 AND event.entity_type = 'navigation_task'
                 AND event.entity_id = expected.id
                 AND event.event_type = expected.event_type
                LEFT JOIN LATERAL (
                    SELECT candidate.id
                    FROM follow_up_request AS candidate
                    WHERE candidate.organization_id = expected.organization_id
                      AND candidate.navigation_task_id = expected.id
                    ORDER BY candidate.id
                    LIMIT 1
                ) AS request ON true
                GROUP BY expected.id, expected.event_type
            )
            SELECT
                navigation_task_id,
                event_type,
                coalesce(audit_event_ids, ARRAY[]::uuid[]) AS audit_event_ids,
                CASE
                    WHEN event_count = 0 THEN 'missing_event'
                    WHEN event_count > 1 THEN 'duplicate_event'
                    ELSE 'inconsistent_event'
                END AS issue,
                event_count
            FROM checked
            WHERE event_count <> 1 OR consistent IS DISTINCT FROM true
            ORDER BY navigation_task_id, event_type
        """,
        identifier_columns=("navigation_task_id", "event_type"),
        evidence_columns=("audit_event_ids", "issue", "event_count"),
    )

    return sorted(
        violations,
        key=lambda violation: (
            violation.category,
            tuple(sorted(violation.identifiers.items())),
            repr(sorted(violation.evidence.items())),
        ),
    )
