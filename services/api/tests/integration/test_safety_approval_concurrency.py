from __future__ import annotations

import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings


def _database_is_reachable(database_url: str) -> bool:
    url = make_url(database_url)
    if not url.host:
        return False
    try:
        with socket.create_connection((url.host, url.port or 5432), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def database_url() -> str:
    if not _database_is_reachable(settings.database_url):
        pytest.skip("PostgreSQL DATABASE_URL is not reachable for safety concurrency tests")
    return settings.database_url


def _seed_committed_race(
    database_url: str,
    *,
    deterministic_level: str = "routine",
    preapprove: bool = False,
) -> dict[str, UUID]:
    engine = create_engine(database_url)
    ids = {name: uuid4() for name in (
        "organization", "proposer", "approver", "second_approver", "patient_author",
        "role", "second_role", "patient", "pathway", "episode", "definition",
        "submission", "rule", "signal", "policy", "proposal",
    )}
    now = datetime.now(UTC)
    try:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO organization (id, name) VALUES (:id, :name)"), {
                "id": ids["organization"], "name": f"Safety race {uuid4()}"})
            for key in ("proposer", "approver", "second_approver", "patient_author"):
                connection.execute(text(
                    "INSERT INTO user_account (id, email, display_name, is_active) "
                    "VALUES (:id, :email, :name, true)"
                ), {"id": ids[key], "email": f"{key}-{uuid4()}@example.test", "name": key})
            connection.execute(text(
                "INSERT INTO role_assignment (id, organization_id, user_id, role, granted_at) "
                "VALUES (:id, :organization_id, :user_id, 'navigator', :granted_at)"
            ), {"id": ids["role"], "organization_id": ids["organization"],
                "user_id": ids["approver"], "granted_at": now - timedelta(hours=1)})
            connection.execute(text(
                "INSERT INTO role_assignment (id, organization_id, user_id, role, granted_at) "
                "VALUES (:id, :organization_id, :user_id, 'navigator', :granted_at)"
            ), {"id": ids["second_role"], "organization_id": ids["organization"],
                "user_id": ids["second_approver"],
                "granted_at": now - timedelta(hours=1)})
            connection.execute(
                text(
                    "INSERT INTO synthetic_patient "
                    "(id, organization_id, external_ref, display_name, demographics) "
                    "VALUES (:id, :organization_id, :external_ref, "
                    "'Race patient', '{}'::jsonb)"
                ),
                {
                    "id": ids["patient"],
                    "organization_id": ids["organization"],
                    "external_ref": f"race-{uuid4()}",
                },
            )
            connection.execute(text(
                "INSERT INTO pathway_definition "
                "(id, organization_id, slug, version, name, configuration, is_active) "
                "VALUES (:id, :organization_id, :slug, 1, 'Race', '{}'::jsonb, true)"
            ), {"id": ids["pathway"], "organization_id": ids["organization"],
                "slug": f"race-{uuid4()}"})
            connection.execute(text(
                "INSERT INTO care_episode (id, organization_id, patient_id, status, started_at) "
                "VALUES (:id, :organization_id, :patient_id, 'active', :now)"
            ), {"id": ids["episode"], "organization_id": ids["organization"],
                "patient_id": ids["patient"], "now": now})
            connection.execute(text(
                "INSERT INTO check_in_definition "
                "(id, organization_id, pathway_definition_id, slug, version, title, questionnaire) "
                "VALUES (:id, :organization_id, :pathway, :slug, 1, 'Race', '{}'::jsonb)"
            ), {"id": ids["definition"], "organization_id": ids["organization"],
                "pathway": ids["pathway"], "slug": f"race-{uuid4()}"})
            connection.execute(text(
                "INSERT INTO check_in_submission "
                "(id, organization_id, patient_id, care_episode_id, check_in_definition_id, "
                "status, answers, submission_source, submitted_by_user_id, submitted_at) "
                "VALUES (:id, :organization_id, :patient_id, :episode_id, :definition_id, "
                "'submitted', '{}'::jsonb, 'patient', :author_id, :now)"
            ), {"id": ids["submission"], "organization_id": ids["organization"],
                "patient_id": ids["patient"], "episode_id": ids["episode"],
                "definition_id": ids["definition"], "author_id": ids["patient_author"],
                "now": now})
            connection.execute(
                text(
                    "INSERT INTO signal_rule "
                    "(id, organization_id, rule_code, version, rule_kind, name) "
                    "VALUES (:id, :organization_id, 'race-rule', 1, "
                    "'deterministic', 'Race rule')"
                ),
                {"id": ids["rule"], "organization_id": ids["organization"]},
            )
            connection.execute(text(
                "INSERT INTO safety_signal "
                "(id, organization_id, patient_id, care_episode_id, source_submission_id, "
                "signal_rule_id, signal_rule_version, deterministic_level, effective_level, "
                "status, evidence, acknowledged_by_user_id, acknowledged_at) "
                "VALUES (:id, :organization_id, :patient_id, :episode_id, :submission_id, "
                ":rule_id, 1, :level, :level, 'acknowledged', '[]'::jsonb, :proposer, :now)"
            ), {"id": ids["signal"], "organization_id": ids["organization"],
                "patient_id": ids["patient"], "episode_id": ids["episode"],
                "submission_id": ids["submission"], "rule_id": ids["rule"],
                "level": deterministic_level, "proposer": ids["proposer"], "now": now})
            connection.execute(text(
                "INSERT INTO approval_policy "
                "(id, organization_id, change_type, version, effective_from, "
                "deterministic_severity_threshold, allow_self_approval, "
                "required_approval_count, required_approver_role) "
                "VALUES (:id, :organization_id, 'dismiss_signal', 1, :effective_from, "
                "'urgent', false, 1, 'navigator')"
            ), {"id": ids["policy"], "organization_id": ids["organization"],
                "effective_from": now - timedelta(days=1)})
            connection.execute(text(
                "INSERT INTO proposed_change "
                "(id, organization_id, proposed_by_user_id, proposed_at, change_type, "
                "proposed_value, rationale, value_schema_id, value_schema_version, "
                "safety_signal_id, approval_policy_id, approval_policy_version, "
                "deterministic_severity_threshold_snapshot, allow_self_approval_snapshot, "
                "required_approval_count_snapshot, required_approver_role_snapshot) "
                "VALUES (:id, :organization_id, :proposer, :now, 'dismiss_signal', "
                "'{\"category\":\"false_positive\"}'::jsonb, 'Race dismissal', "
                "'ojcc.dismiss-signal', 1, :signal_id, :policy_id, 1, 'urgent', "
                "false, 1, 'navigator')"
            ), {"id": ids["proposal"], "organization_id": ids["organization"],
                "proposer": ids["proposer"], "now": now, "signal_id": ids["signal"],
                "policy_id": ids["policy"]})
            if preapprove:
                connection.execute(text(
                    "INSERT INTO approval_decision "
                    "(id, organization_id, proposed_change_id, authorized_by_user_id, "
                    "qualifying_role_assignment_id, qualifying_role_snapshot, decision, "
                    "authorized_at) VALUES (:id, :organization_id, :proposal_id, :user_id, "
                    ":role_id, 'navigator', 'approved', :now)"
                ), {"id": uuid4(), "organization_id": ids["organization"],
                    "proposal_id": ids["proposal"], "user_id": ids["approver"],
                    "role_id": ids["role"], "now": now})
    finally:
        engine.dispose()
    return ids


def _cleanup(database_url: str, ids: dict[str, UUID]) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("SET session_replication_role = replica"))
            for table in (
                "approval_decision", "safety_signal_resolution", "safety_signal",
                "proposed_change", "approval_policy", "signal_rule", "check_in_submission",
                "care_episode", "check_in_definition", "pathway_definition", "role_assignment",
                "synthetic_patient", "user_account", "organization",
            ):
                if table == "user_account":
                    continue
                column = "id" if table == "organization" else "organization_id"
                connection.execute(
                    text(f"DELETE FROM {table} WHERE {column} = :organization_id"),
                    {"organization_id": ids["organization"]},
                )
            connection.execute(
                text(
                    "DELETE FROM user_account WHERE id IN "
                    "(:proposer, :approver, :second_approver, :patient_author)"
                ),
                {
                    "proposer": ids["proposer"],
                    "approver": ids["approver"],
                    "second_approver": ids["second_approver"],
                    "patient_author": ids["patient_author"],
                },
            )
            connection.execute(text("SET session_replication_role = origin"))
    finally:
        engine.dispose()


def _expected_database_rejection(error: SQLAlchemyError, expected_message: str) -> str:
    original = getattr(error, "orig", error)
    if getattr(original, "sqlstate", None) != "P0001" or expected_message not in str(original):
        raise error
    return "rejected"


@pytest.mark.parametrize("winning_path", ["resolution", "dismissal"])
def test_concurrent_resolution_and_final_dismissal_allow_exactly_one_terminal_path(
    database_url: str,
    winning_path: str,
) -> None:
    ids = _seed_committed_race(database_url)
    engine = create_engine(database_url)
    first_has_lock = Event()
    release_first = Event()
    second_started = Event()
    now = datetime.now(UTC)

    def resolve(first: bool) -> str:
        try:
            with engine.begin() as connection:
                if not first:
                    second_started.set()
                connection.execute(text(
                    "INSERT INTO safety_signal_resolution "
                    "(id, organization_id, safety_signal_id, resolved_by_user_id, resolved_at, "
                    "resolution_reason) VALUES (:id, :organization_id, :signal_id, :user_id, "
                    ":now, 'Resolved concurrently')"
                ), {"id": uuid4(), "organization_id": ids["organization"],
                    "signal_id": ids["signal"], "user_id": ids["proposer"], "now": now})
                if first:
                    first_has_lock.set()
                    assert release_first.wait(timeout=10)
            return "resolved"
        except SQLAlchemyError as error:
            return _expected_database_rejection(
                error,
                "Dismissed safety signal cannot be resolved",
            )

    def dismiss(first: bool) -> str:
        try:
            with engine.begin() as connection:
                if not first:
                    second_started.set()
                connection.execute(text(
                    "INSERT INTO approval_decision "
                    "(id, organization_id, proposed_change_id, authorized_by_user_id, "
                    "qualifying_role_assignment_id, qualifying_role_snapshot, decision, "
                    "authorized_at) VALUES (:id, :organization_id, :proposal_id, :user_id, "
                    ":role_id, 'navigator', 'approved', :now)"
                ), {"id": uuid4(), "organization_id": ids["organization"],
                    "proposal_id": ids["proposal"], "user_id": ids["approver"],
                    "role_id": ids["role"], "now": now})
                if first:
                    first_has_lock.set()
                    assert release_first.wait(timeout=10)
            return "dismissed"
        except SQLAlchemyError as error:
            return _expected_database_rejection(
                error,
                "Resolved safety signal cannot be dismissed",
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            if winning_path == "resolution":
                first_future = executor.submit(resolve, True)
                assert first_has_lock.wait(timeout=10)
                second_future = executor.submit(dismiss, False)
                expected_winner = "resolved"
            else:
                first_future = executor.submit(dismiss, True)
                assert first_has_lock.wait(timeout=10)
                second_future = executor.submit(resolve, False)
                expected_winner = "dismissed"
            assert second_started.wait(timeout=10)
            release_first.set()
            results = [first_future.result(timeout=20), second_future.result(timeout=20)]
        assert results == [expected_winner, "rejected"]
        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT effective_state, "
                "(SELECT count(*) FROM safety_signal_resolution r "
                " WHERE r.safety_signal_id = state.id) AS resolutions, "
                "(SELECT count(*) FROM safety_signal s "
                " WHERE s.id = state.id "
                " AND s.dismissal_proposed_change_id IS NOT NULL) AS dismissals "
                "FROM effective_safety_signal_state state WHERE id = :signal_id"
            ), {"signal_id": ids["signal"]}).mappings().one()
        assert row.effective_state in {"resolved", "dismissed"}
        assert row.resolutions + row.dismissals == 1
    finally:
        engine.dispose()
        _cleanup(database_url, ids)


@pytest.mark.parametrize("mutation", ["backdated_revocation", "role_change"])
@pytest.mark.parametrize("winner", ["mutation", "approval"])
def test_final_approval_serializes_with_invalidating_role_history_update(
    database_url: str,
    mutation: str,
    winner: str,
) -> None:
    ids = _seed_committed_race(
        database_url,
        deterministic_level="urgent",
        preapprove=True,
    )
    engine = create_engine(database_url)
    first_has_lock = Event()
    release_first = Event()
    second_started = Event()
    authorized_at = datetime.now(UTC)

    def approve(first: bool) -> str:
        with engine.begin() as connection:
            if not first:
                second_started.set()
            connection.execute(text(
                "INSERT INTO approval_decision "
                "(id, organization_id, proposed_change_id, authorized_by_user_id, "
                "qualifying_role_assignment_id, qualifying_role_snapshot, decision, "
                "authorized_at) VALUES (:id, :organization_id, :proposal_id, :user_id, "
                ":role_id, 'navigator', 'approved', :authorized_at)"
            ), {"id": uuid4(), "organization_id": ids["organization"],
                "proposal_id": ids["proposal"], "user_id": ids["second_approver"],
                "role_id": ids["second_role"], "authorized_at": authorized_at})
            if first:
                first_has_lock.set()
                assert release_first.wait(timeout=10)
        return "approval_committed"

    def mutate(first: bool) -> str:
        try:
            with engine.begin() as connection:
                if not first:
                    second_started.set()
                if mutation == "backdated_revocation":
                    connection.execute(
                        text(
                            "UPDATE role_assignment SET revoked_at = :revoked_at "
                            "WHERE id = :role_id"
                        ),
                        {
                            "revoked_at": authorized_at - timedelta(minutes=30),
                            "role_id": ids["role"],
                        },
                    )
                else:
                    connection.execute(
                        text(
                            "UPDATE role_assignment SET role = 'administrator' "
                            "WHERE id = :role_id"
                        ),
                        {"role_id": ids["role"]},
                    )
                if first:
                    first_has_lock.set()
                    assert release_first.wait(timeout=10)
            return "mutation_committed"
        except SQLAlchemyError as error:
            return _expected_database_rejection(
                error,
                "Role assignment mutation would invalidate approval history",
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            if winner == "mutation":
                first_future = executor.submit(mutate, True)
                assert first_has_lock.wait(timeout=10)
                second_future = executor.submit(approve, False)
            else:
                first_future = executor.submit(approve, True)
                assert first_has_lock.wait(timeout=10)
                second_future = executor.submit(mutate, False)
            assert second_started.wait(timeout=10)
            release_first.set()
            outcomes = [first_future.result(timeout=20), second_future.result(timeout=20)]

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT state.effective_state, signal.dismissal_proposed_change_id "
                    "FROM effective_proposed_change_state state "
                    "JOIN safety_signal signal ON signal.id = state.safety_signal_id "
                    "WHERE state.id = :proposal_id"
                ),
                {"proposal_id": ids["proposal"]},
            ).mappings().one()
        if winner == "mutation":
            assert outcomes == ["mutation_committed", "approval_committed"]
            assert row.effective_state == "pending"
            assert row.dismissal_proposed_change_id is None
        else:
            assert outcomes == ["approval_committed", "rejected"]
            assert row.effective_state == "approved"
            assert row.dismissal_proposed_change_id == ids["proposal"]
    finally:
        engine.dispose()
        _cleanup(database_url, ids)


def test_two_concurrent_high_risk_approvals_apply_once_from_two_qualified_humans(
    database_url: str,
) -> None:
    ids = _seed_committed_race(database_url, deterministic_level="urgent")
    engine = create_engine(database_url)
    barrier = Barrier(2)
    authorized_at = datetime.now(UTC)

    def approve(user_key: str, role_key: str) -> str:
        barrier.wait(timeout=10)
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO approval_decision "
                "(id, organization_id, proposed_change_id, authorized_by_user_id, "
                "qualifying_role_assignment_id, qualifying_role_snapshot, decision, "
                "authorized_at) VALUES (:id, :organization_id, :proposal_id, :user_id, "
                ":role_id, 'navigator', 'approved', :authorized_at)"
            ), {"id": uuid4(), "organization_id": ids["organization"],
                "proposal_id": ids["proposal"], "user_id": ids[user_key],
                "role_id": ids[role_key], "authorized_at": authorized_at})
        return "committed"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(approve, "approver", "role")
            second = executor.submit(approve, "second_approver", "second_role")
            assert [first.result(timeout=20), second.result(timeout=20)] == [
                "committed",
                "committed",
            ]
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT state.effective_state, signal.dismissal_proposed_change_id, "
                    "(SELECT count(*) FROM approval_decision decision "
                    " WHERE decision.proposed_change_id = state.id) AS decision_count "
                    "FROM effective_proposed_change_state state "
                    "JOIN safety_signal signal ON signal.id = state.safety_signal_id "
                    "WHERE state.id = :proposal_id"
                ),
                {"proposal_id": ids["proposal"]},
            ).mappings().one()
        assert row.effective_state == "approved"
        assert row.dismissal_proposed_change_id == ids["proposal"]
        assert row.decision_count == 2
    finally:
        engine.dispose()
        _cleanup(database_url, ids)
