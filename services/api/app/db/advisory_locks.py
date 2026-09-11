"""Stable transaction locks for aggregates the runtime may read but not update."""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def acquire_transaction_lock(
    session: Session, *, namespace: str, identifier: UUID
) -> None:
    """Serialize one aggregate without granting UPDATE on its read-only root row."""
    lock_material = f"{namespace}:{identifier}".encode()
    lock_key = int.from_bytes(sha256(lock_material).digest()[:8], "big", signed=True)
    session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": lock_key},
    )
