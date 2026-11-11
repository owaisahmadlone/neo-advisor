"""Keyed random streams: each draw depends only on (seed, what, which), never on call order.

That keeps the truth plant replayable day by day and lets the harness run several
policies against the same plant without the draws drifting apart.
"""

from __future__ import annotations

import zlib
from datetime import date

import numpy as np

Key = str | int | date


def _entropy(key: Key) -> int:
    match key:
        case date():
            return key.toordinal()
        case int():
            return key if key >= 0 else (1 << 32) + key
        case str():
            return zlib.crc32(key.encode())
        case _:
            raise TypeError(f"unsupported seed key {key!r}")


def keyed_rng(seed: int, *keys: Key) -> np.random.Generator:
    return np.random.default_rng([seed, *(_entropy(k) for k in keys)])
