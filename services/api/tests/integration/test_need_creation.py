"""The creation boundary is exercised with the real restricted PostgreSQL login."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db.models import CheckInSubmission
from scripts.seed_demo import DEMO_IDS, seed_demo
from tests.database_support import disposable_database


@pytest.fixture(scope="module")
def creation_database():
    with disposable_database(prefix="ojcc_task7_", migrate_to="head") as database:
        owner = create_engine(database.migration_url)
        runtime = create_engine(database.application_url)
        try:
            with Session(owner) as session:
                seed_demo(session)
                session.commit()
            yield owner, runtime, database
        finally:
            runtime.dispose()
            owner.dispose()


def submission(engine, predecessor=None, value="yes"):
    with Session(engine) as session:
        source = session.get(CheckInSubmission, DEMO_IDS["submission_v2"])
        answers = deepcopy(source.answers)
        for item in answers["items"]:
            if item["link_id"] == "transportation":
                item["value"] = value
        row = CheckInSubmission(
            id=uuid4(),
            organization_id=source.organization_id,
            patient_id=source.patient_id,
            care_episode_id=source.care_episode_id,
            check_in_definition_id=source.check_in_definition_id,
            status="submitted",
            answers=answers,
            submission_source="patient",
            submitted_by_user_id=source.submitted_by_user_id,
            supersedes_submission_id=predecessor,
            submitted_at=datetime.now(UTC),
        )
        session.add(row)
        session.flush()
        result = row.id
        session.commit()
        return result


def propose(connection, root, leaf=None):
    return (
        connection.execute(
            text("""
        INSERT INTO need_creation_proposal
            (id, organization_id, chain_root_id, source_submission_id,
             proposed_by_user_id, rationale)
        VALUES (:id, :org, :root, :leaf, :actor, 'Review synthetic transportation report')
        RETURNING *
    """),
            dict(
                id=uuid4(),
                org=DEMO_IDS["organization"],
                root=root,
                leaf=leaf or root,
                actor=DEMO_IDS["navigator_user"],
            ),
        )
        .mappings()
        .one()
    )


def decide(connection, proposal_id, request_id=None, decision="approved"):
    request_id = request_id or uuid4()
    connection.execute(
        text("""
        INSERT INTO need_creation_decision
            (id, organization_id, proposal_id, authorized_by_user_id, decision, reason)
        VALUES (:id, :org, :proposal, :actor, :decision, 'Synthetic review')
    """),
        dict(
            id=request_id,
            org=DEMO_IDS["organization"],
            proposal=proposal_id,
            actor=DEMO_IDS["navigator_user"],
            decision=decision,
        ),
    )
    return (
        connection.execute(
            text("SELECT * FROM need_creation_decision WHERE id=:id"), dict(id=request_id)
        )
        .mappings()
        .one()
    )


def test_approval_creates_exact_need_and_audit_with_database_derived_evidence(creation_database):
    _, runtime, _ = creation_database
    root = submission(runtime)
    with runtime.begin() as connection:
        proposal = propose(connection, root)
        assert proposal["evidence"]["source_submission_id"] == str(root)
        assert proposal["evidence"]["answer"]["value"] == "yes"
        assert (
            proposal["evidence_sha256"]
            == hashlib.sha256(proposal["canonical_evidence"].encode("utf-8")).hexdigest()
        )
        assert proposal["policy_snapshot"]["allow_self_approval"] is True
        result = decide(connection, proposal["id"])
        assert result["outcome"] == "created"
        need = (
            connection.execute(
                text("SELECT * FROM reported_need WHERE id=:id"),
                dict(id=result["reported_need_id"]),
            )
            .mappings()
            .one()
        )
        assert need["status"] == "open"
        assert need["kind"] == "transportation"
        assert need["source_submission_id"] == root
        assert (
            connection.scalar(
                text("SELECT count(*) FROM audit_event WHERE entity_id=:id"), dict(id=result["id"])
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM navigation_task WHERE reported_need_id=:id"),
                dict(id=need["id"]),
            )
            == 0
        )


@pytest.mark.parametrize("value", ["yes", "no"])
def test_correction_before_approval_records_refusal_without_creating(creation_database, value):
    _, runtime, _ = creation_database
    root = submission(runtime)
    with runtime.begin() as connection:
        proposal = propose(connection, root)
    submission(runtime, root, value)
    with runtime.begin() as connection:
        result = decide(connection, proposal["id"])
        assert result["outcome"] == "stale_evidence"
        assert result["reported_need_id"] is None
    with runtime.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM audit_event WHERE entity_id=:id"), dict(id=result["id"])
            )
            == 1
        )


def test_retries_and_independent_reports(creation_database):
    _, runtime, _ = creation_database
    roots = [submission(runtime), submission(runtime)]
    need_ids = []
    for root in roots:
        with runtime.begin() as connection:
            proposal = propose(connection, root)
            second = propose(connection, root)
            request_id = uuid4()
            result = decide(connection, proposal["id"], request_id)
            retry = decide(connection, proposal["id"], request_id)
            assert retry == result
            refused = decide(connection, second["id"])
            assert refused["outcome"] == "already_created"
            need_ids.append(result["reported_need_id"])
    assert len(set(need_ids)) == 2


def test_correction_after_creation_preserves_original_evidence(creation_database):
    _, runtime, _ = creation_database
    root = submission(runtime)
    with runtime.begin() as connection:
        proposal = propose(connection, root)
        result = decide(connection, proposal["id"])
    submission(runtime, root, "no")
    with runtime.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT source_submission_id FROM reported_need WHERE id=:id"),
                dict(id=result["reported_need_id"]),
            )
            == root
        )


def test_concurrent_approvals_create_one_need(creation_database):
    _, runtime, _ = creation_database
    root = submission(runtime)
    with runtime.begin() as connection:
        proposals = [propose(connection, root)["id"] for _ in range(2)]
    barrier = Barrier(2)

    def approve(proposal_id):
        with runtime.begin() as connection:
            barrier.wait(timeout=10)
            return decide(connection, proposal_id)["outcome"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(approve, proposals)) == ["already_created", "created"]


def test_runtime_cannot_bypass_creation_or_mutate_history(creation_database):
    _, runtime, _ = creation_database
    root = submission(runtime)
    with runtime.begin() as connection:
        proposal = propose(connection, root)
    for sql in (
        "UPDATE need_creation_proposal SET rationale='changed' WHERE id=:id",
        "DELETE FROM need_creation_proposal WHERE id=:id",
        "INSERT INTO reported_need (id) VALUES (:id)",
        "UPDATE need_creation_policy SET allow_self_approval=false",
    ):
        with runtime.begin() as connection, pytest.raises(DBAPIError):
            connection.execute(text(sql), dict(id=proposal["id"]))


def test_transaction_rollback_removes_decision_and_need(creation_database):
    _, runtime, _ = creation_database
    root = submission(runtime)
    with runtime.begin() as connection:
        proposal = propose(connection, root)
    with runtime.connect() as connection:
        transaction = connection.begin()
        result = decide(connection, proposal["id"])
        transaction.rollback()
    with runtime.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM reported_need WHERE id=:id"),
                dict(id=result["reported_need_id"]),
            )
            == 0
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM need_creation_decision WHERE id=:id"),
                dict(id=result["id"]),
            )
            == 0
        )


def test_runtime_attests_exact_new_boundary(creation_database):
    from app.db.privilege_attestation import attest_runtime_database
    from app.db.targets import parse_database_target

    _, runtime, database = creation_database
    with runtime.connect() as connection:
        attest_runtime_database(
            connection, target=parse_database_target(database.application_url, label="DATABASE_URL")
        )


def test_decline_requires_reason_at_database_boundary(creation_database):
    _, runtime, _ = creation_database
    root = submission(runtime)
    with runtime.begin() as connection:
        proposal = propose(connection, root)
    with runtime.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text("""
            INSERT INTO need_creation_decision
            (id,organization_id,proposal_id,authorized_by_user_id,decision,reason)
            VALUES (:id,:org,:proposal,:actor,'declined',NULL)
        """),
            dict(
                id=uuid4(),
                org=DEMO_IDS["organization"],
                proposal=proposal["id"],
                actor=DEMO_IDS["navigator_user"],
            ),
        )


def test_stale_proposal_cannot_silently_select_new_leaf(creation_database):
    _, runtime, _ = creation_database
    root = submission(runtime)
    leaf = submission(runtime, root)
    with runtime.begin() as connection, pytest.raises(DBAPIError, match="stale_evidence"):
        propose(connection, root)
    with runtime.begin() as connection:
        proposal = propose(connection, root, leaf)
        assert proposal["source_submission_id"] == leaf


def test_direct_sql_fabricated_evidence_is_overwritten(creation_database):
    _, runtime, _ = creation_database
    root = submission(runtime)
    with runtime.begin() as connection:
        result = (
            connection.execute(
                text("""
            INSERT INTO need_creation_proposal
            (id,organization_id,chain_root_id,source_submission_id,proposed_by_user_id,
             rationale,evidence,policy_snapshot)
            VALUES (:id,:org,:root,:root,:actor,'Synthetic review',
                '{"answer":{"value":"fabricated"}}', '{"required_approval_count": 0}')
            RETURNING evidence,policy_snapshot
        """),
                dict(
                    id=uuid4(),
                    org=DEMO_IDS["organization"],
                    root=root,
                    actor=DEMO_IDS["navigator_user"],
                ),
            )
            .mappings()
            .one()
        )
        assert result["evidence"]["answer"]["value"] == "yes"
        assert result["policy_snapshot"]["required_approval_count"] == 1


@pytest.mark.parametrize("first", ["correction", "approval"])
def test_correction_and_approval_share_database_lock(creation_database, first):
    from threading import Event

    _, runtime, _ = creation_database
    root = submission(runtime)
    with runtime.begin() as connection:
        proposal = propose(connection, root)
    started = Event()
    with runtime.connect() as connection:
        transaction = connection.begin()
        if first == "correction":
            connection.execute(
                text("""
                INSERT INTO check_in_submission
                (id,organization_id,patient_id,care_episode_id,check_in_definition_id,status,
                 answers,submission_source,submitted_by_user_id,supersedes_submission_id,submitted_at)
                SELECT :new,id_org,patient_id,care_episode_id,check_in_definition_id,status,
                    answers,submission_source,submitted_by_user_id,:root,clock_timestamp()
                FROM (SELECT *,organization_id AS id_org FROM check_in_submission WHERE id=:root) s
            """),
                dict(new=uuid4(), root=root),
            )
        else:
            decide(connection, proposal["id"])

        def second():
            started.set()
            if first == "approval":
                return submission(runtime, root, "no")
            with runtime.begin() as other:
                return decide(other, proposal["id"])["outcome"]

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(second)
            assert started.wait(timeout=5)
            # Inspect the database wait, not a timing-only assumption.
            import time

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with runtime.connect() as observer:
                    waiting = observer.scalar(
                        text("""
                        SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()
                        AND wait_event='advisory'
                    """)
                    )
                if waiting:
                    break
                time.sleep(0.02)
            assert waiting, "second operation did not wait on chain lock"
            transaction.commit()
            result = future.result(timeout=10)
            if first == "correction":
                assert result == "stale_evidence"


def test_integrity_audit_detects_restored_fabricated_snapshot(creation_database):
    from app.db.integrity import inspect_integrity
    from tests.database_support import user_triggers_disabled

    owner, runtime, _ = creation_database
    root = submission(runtime)
    with runtime.begin() as connection:
        proposal = propose(connection, root)
    with Session(owner) as session:
        with user_triggers_disabled(session, "need_creation_proposal"):
            session.execute(
                text("""UPDATE need_creation_proposal SET evidence_sha256=repeat('0',64)
                WHERE id=:id"""),
                dict(id=proposal["id"]),
            )
        violations = inspect_integrity(session)
        assert any(v.category == "need_creation_evidence_mismatch" for v in violations)
        session.rollback()


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_commands_refuse_stale_transaction_snapshot_isolation(creation_database, isolation):
    _, runtime, _ = creation_database
    root = submission(runtime)
    with runtime.connect().execution_options(isolation_level=isolation) as connection:
        with connection.begin(), pytest.raises(DBAPIError, match="read_committed_required"):
            propose(connection, root)


def test_authority_expiring_during_chain_wait_is_rechecked(creation_database):
    import time

    owner, runtime, _ = creation_database
    root = submission(runtime)
    actor = uuid4()
    with runtime.begin() as connection:
        proposal = propose(connection, root)
    with owner.begin() as connection:
        connection.execute(
            text("""INSERT INTO user_account(id,email,display_name,is_active)
            VALUES (:id,:email,'Synthetic expiring navigator',true)"""),
            dict(id=actor, email=f"{actor}@example.test"),
        )
        connection.execute(
            text("""INSERT INTO role_assignment
            (id,organization_id,user_id,role,granted_at,revoked_at)
            VALUES (:id,:org,:actor,'navigator',clock_timestamp()-interval '1 day',
                clock_timestamp()+interval '2 seconds')"""),
            dict(id=uuid4(), org=DEMO_IDS["organization"], actor=actor),
        )
    with owner.connect() as blocker:
        transaction = blocker.begin()
        blocker.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            dict(key=f"need-creation:{DEMO_IDS['organization']}:{root}"),
        )

        def approve():
            with runtime.begin() as connection:
                connection.execute(
                    text("""INSERT INTO need_creation_decision
                    (id,organization_id,proposal_id,authorized_by_user_id,decision)
                    VALUES (:id,:org,:proposal,:actor,'approved')"""),
                    dict(
                        id=uuid4(),
                        org=DEMO_IDS["organization"],
                        proposal=proposal["id"],
                        actor=actor,
                    ),
                )

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(approve)
            deadline = time.monotonic() + 1
            waiting = False
            while time.monotonic() < deadline:
                with runtime.connect() as observer:
                    waiting = observer.scalar(
                        text("""SELECT count(*) FROM pg_stat_activity
                        WHERE datname=current_database() AND wait_event='advisory'""")
                    )
                if waiting:
                    break
                time.sleep(0.02)
            # Always release the blocker, including on test failure.
            time.sleep(2.1)
            transaction.commit()
            assert waiting, "approval did not reach the chain lock before authority expired"
            with pytest.raises(DBAPIError, match="navigator_authority_invalid"):
                future.result(timeout=10)


def test_new_overlapping_authority_waits_for_approval_transaction(creation_database):
    import time

    owner, runtime, _ = creation_database
    root = submission(runtime)
    assignment_id = uuid4()
    with runtime.begin() as connection:
        proposal = propose(connection, root)
    try:
        with runtime.connect() as connection:
            transaction = connection.begin()
            decide(connection, proposal["id"])

            def insert_overlap():
                with owner.begin() as writer:
                    writer.execute(
                        text("""INSERT INTO role_assignment
                        (id,organization_id,user_id,role,granted_at,revoked_at)
                        VALUES (:id,:org,:actor,'navigator',clock_timestamp()-interval '1 hour',
                            clock_timestamp()+interval '1 hour')"""),
                        dict(
                            id=assignment_id,
                            org=DEMO_IDS["organization"],
                            actor=DEMO_IDS["navigator_user"],
                        ),
                    )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(insert_overlap)
                deadline = time.monotonic() + 2
                waiting = False
                while time.monotonic() < deadline and not future.done():
                    with runtime.connect() as observer:
                        waiting = observer.scalar(
                            text("""SELECT count(*) FROM pg_locks
                            WHERE locktype='advisory' AND NOT granted
                            AND database=(SELECT oid FROM pg_database
                                WHERE datname=current_database())""")
                        )
                    if waiting:
                        break
                    time.sleep(0.02)
                transaction.commit()
                future.result(timeout=10)
                assert waiting, "authority insertion did not serialize with approval"
        with (
            runtime.begin() as connection,
            pytest.raises(DBAPIError, match="navigator_authority_invalid"),
        ):
            propose(connection, submission(runtime))
    finally:
        with owner.begin() as cleanup:
            cleanup.execute(
                text("DELETE FROM role_assignment WHERE id=:id"), dict(id=assignment_id)
            )
