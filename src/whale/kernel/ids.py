"""Identifiers: ULIDs (Crockford base32) with optional type prefixes.

IDs sort by creation time, which keeps SQLite indexes and event logs ordered
without an extra column.
"""

from __future__ import annotations

import os
import threading
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_lock = threading.Lock()
_last_ms = 0
_last_rand = 0


def now_ms() -> int:
    return int(time.time() * 1000)


def ulid() -> str:
    """Monotonic ULID: within the same millisecond the random part increments."""
    global _last_ms, _last_rand
    with _lock:
        ms = now_ms()
        if ms == _last_ms:
            _last_rand = (_last_rand + 1) & ((1 << 80) - 1)
        else:
            _last_ms = ms
            _last_rand = int.from_bytes(os.urandom(10), "big")
        value = (ms << 80) | _last_rand
    chars = []
    for _ in range(26):
        chars.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new_id(prefix: str) -> str:
    return f"{prefix}_{ulid()}"


def ulid_ms(value: str) -> int:
    """Timestamp embedded in a (possibly prefixed) ULID."""
    raw = value.rsplit("_", 1)[-1]
    n = 0
    for ch in raw[:10]:
        n = n * 32 + _ALPHABET.index(ch)
    return n
