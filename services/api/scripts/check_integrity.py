from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.db.integrity import inspect_integrity


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only integrity audit required after trigger-bypassing restores or loads."
    )
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="PostgreSQL URL to inspect (defaults to DATABASE_URL).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    engine = create_engine(arguments.database_url, pool_pre_ping=True)
    try:
        with Session(engine) as session:
            violations = inspect_integrity(session)
    finally:
        engine.dispose()

    payload = {
        "status": "violations_found" if violations else "ok",
        "violation_count": len(violations),
        "violations": [violation.as_dict() for violation in violations],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
