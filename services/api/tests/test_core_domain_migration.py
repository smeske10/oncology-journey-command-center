import hashlib
import subprocess
import sys
from pathlib import Path

from app.db.base import Base

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0001_core_domain.py"
)
EXPECTED_INITIAL_MIGRATION_SHA256 = (
    "a177b32040c760e52ffd64872f61104f2064968aa6981295c54728e518cb6391"
)


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
