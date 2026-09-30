"""Time-ordered UUIDv7 identifiers.

Exposed ids are UUIDs; version 7 keeps inserts index-local (random v4 keys scatter the
B-tree). Postgres 16 has no native generator and Python 3.12 has no `uuid.uuid7`, so the
RFC 9562 layout is built here: 48-bit Unix milliseconds, version, 74 random bits.
A per-process counter in the random field keeps ids monotonic within one millisecond.
"""

from __future__ import annotations

import os
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_counter = 0


def uuid7() -> uuid.UUID:
    global _last_ms, _counter
    with _lock:
        now_ms = time.time_ns() // 1_000_000
        if now_ms == _last_ms:
            _counter = (_counter + 1) & 0xFFF
            if _counter == 0:  # 4096 ids in one ms: wait for the clock
                while now_ms <= _last_ms:
                    now_ms = time.time_ns() // 1_000_000
        else:
            _counter = int.from_bytes(os.urandom(2), "big") & 0x7FF
        _last_ms = now_ms
        rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
        value = (now_ms & ((1 << 48) - 1)) << 80
        value |= 0x7 << 76
        value |= _counter << 64
        value |= 0b10 << 62
        value |= rand_b
        return uuid.UUID(int=value)
