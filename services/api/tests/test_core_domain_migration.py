# ruff: noqa: E501

import hashlib
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text

from app.config import settings
from app.db.base import Base

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0001_core_domain.py"
)
EXPECTED_INITIAL_MIGRATION_SHA256 = (
    "a177b32040c760e52ffd64872f61104f2064968aa6981295c54728e518cb6391"
)


def _run_alembic(revision: str) -> subprocess.CompletedProcess[str]:
    project_root = MIGRATION_PATH.parents[4]
    environment = os.environ | {"DATABASE_URL": settings.database_url}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "services/api/alembic.ini",
            "upgrade",
            revision,
        ],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )


def _reset_synthetic_schema() -> None:
    engine = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT current_database()")).scalar_one() == "ojcc"
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
        connection.execute(text("GRANT ALL ON SCHEMA public TO PUBLIC"))
    engine.dispose()


def test_initial_migration_is_an_immutable_explicit_schema_snapshot() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert (
        hashlib.sha256(MIGRATION_PATH.read_bytes()).hexdigest()
        == EXPECTED_INITIAL_MIGRATION_SHA256
    )
    assert "app.db.models" not in source
    assert "app.db.base" not in source
    assert "Base.metadata" not in source
    assert "op.create_table" in source
    assert "op.drop_table" in source

    project_root = MIGRATION_PATH.parents[4]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "services/api/alembic.ini",
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    sql = result.stdout
    migration_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(MIGRATION_PATH.parent.glob("*.py"))
    )
    for table in Base.metadata.tables.values():
        assert f'"{table.name}"' in migration_sources
        assert f"CREATE TABLE {table.name}" in sql
        for constraint in table.constraints:
            if constraint.name and len(constraint.name) <= 63:
                assert constraint.name in sql
        for index in table.indexes:
            assert index.name in sql


def test_upgrade_from_representative_0001_rows_preserves_unambiguous_history() -> None:
    """This fails if reconciliation rejects legacy rows whose identity and provenance are explicit."""
    _reset_synthetic_schema()
    _run_alembic("0001_core_domain")
    organization_id = uuid4()
    patient_user_id = uuid4()
    pathway_id = uuid4()
    episode_id = uuid4()
    definition_id = uuid4()
    submission_id = uuid4()
    created_at = datetime(2026, 8, 18, tzinfo=UTC)
    engine = create_engine(settings.database_url)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO organization (id, name) VALUES (:id, :name)"),
            {"id": organization_id, "name": "Representative synthetic organization"},
        )
        connection.execute(
            text(
                "INSERT INTO user_account "
                "(id, organization_id, email, display_name, is_active, created_at) "
                "VALUES (:id, :organization_id, :email, :display_name, true, :created_at)"
            ),
            {
                "id": patient_user_id,
                "organization_id": organization_id,
                "email": "representative.patient@example.test",
                "display_name": "Representative Patient",
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                "INSERT INTO synthetic_patient "
                "(id, organization_id, external_ref, display_name, demographics, created_at) "
                "VALUES (:id, :organization_id, :external_ref, :display_name, '{}'::jsonb, :created_at)"
            ),
            {
                "id": patient_user_id,
                "organization_id": organization_id,
                "external_ref": "representative-patient",
                "display_name": "Representative Patient",
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                "INSERT INTO role_assignment (id, organization_id, user_id, role, created_at) "
                "VALUES (:id, :organization_id, :user_id, 'supporting_actor', :created_at)"
            ),
            {"id": uuid4(), "organization_id": organization_id, "user_id": patient_user_id, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO pathway_definition "
                "(id, organization_id, slug, version, name, configuration, is_active, created_at) "
                "VALUES (:id, :organization_id, 'representative', 1, 'Representative', '{}'::jsonb, true, :created_at)"
            ),
            {"id": pathway_id, "organization_id": organization_id, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO care_episode "
                "(id, organization_id, patient_id, pathway_definition_id, status, started_at) "
                "VALUES (:id, :organization_id, :patient_id, :pathway_id, 'active', :started_at)"
            ),
            {"id": episode_id, "organization_id": organization_id, "patient_id": patient_user_id, "pathway_id": pathway_id, "started_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO check_in_definition "
                "(id, organization_id, pathway_definition_id, slug, version, title, questionnaire, created_at) "
                "VALUES (:id, :organization_id, :pathway_id, 'weekly', 1, 'Weekly', '{}'::jsonb, :created_at)"
            ),
            {"id": definition_id, "organization_id": organization_id, "pathway_id": pathway_id, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO check_in_submission "
                "(id, organization_id, patient_id, check_in_definition_id, status, answers, submitted_at, created_at) "
                "VALUES (:id, :organization_id, :patient_id, :definition_id, 'submitted', '{}'::jsonb, :submitted_at, :created_at)"
            ),
            {"id": submission_id, "organization_id": organization_id, "patient_id": patient_user_id, "definition_id": definition_id, "submitted_at": created_at, "created_at": created_at},
        )
    engine.dispose()

    _run_alembic("head")

    engine = create_engine(settings.database_url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT patient_id FROM patient_identity_link")).scalar_one() == patient_user_id
        assert connection.execute(text("SELECT care_episode_id FROM check_in_submission")).scalar_one() == episode_id
        assert connection.execute(text("SELECT submitted_by_user_id FROM check_in_submission")).scalar_one() == patient_user_id
    engine.dispose()
