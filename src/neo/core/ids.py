"""Deterministic identifiers: the same inputs always give the same run id."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def stable_hash(payload: Any, *, length: int = 16) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:length]


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 16):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def tree_hashes(root: Path) -> dict[str, str]:
    """Hash every file under ``root``, keyed by its relative path."""
    return {str(path.relative_to(root)): file_hash(path) for path in sorted(root.rglob("*")) if path.is_file()}
