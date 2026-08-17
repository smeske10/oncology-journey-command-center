import subprocess
import sys
from pathlib import Path

from app.db.base import Base

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0001_core_domain.py"
)


def test_initial_migration_is_an_immutable_explicit_schema_snapshot() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

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
    for table in Base.metadata.tables.values():
        assert f'"{table.name}"' in source
        assert f"CREATE TABLE {table.name}" in sql
        for constraint in table.constraints:
            if constraint.name:
                assert constraint.name in sql
        for index in table.indexes:
            assert index.name in sql
