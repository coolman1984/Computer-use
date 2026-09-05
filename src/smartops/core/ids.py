"""Short, time-ordered ids (ULID-like) to keep sorting and tracing easy."""

from __future__ import annotations

import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new_id(prefix: str) -> str:
    """Example: run_01JQ8Z4T2M9K7X"""
    timestamp = _encode(int(time.time() * 1000), 10)
    randomness = _encode(secrets.randbits(30), 6)
    return f"{prefix}_{timestamp}{randomness}"
