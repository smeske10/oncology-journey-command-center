from __future__ import annotations

import secrets
import time
from uuid import UUID


def uuid7() -> UUID:
    """Create a sortable RFC 9562 UUID version 7 without a database dependency."""
    unix_ms = time.time_ns() // 1_000_000
    random_a = secrets.randbits(12)
    random_b = secrets.randbits(62)
    value = (unix_ms << 80) | (0x7 << 76) | (random_a << 64) | (0b10 << 62) | random_b
    return UUID(int=value)
