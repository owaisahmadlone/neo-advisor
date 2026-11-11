from __future__ import annotations

import numpy as np
import pandas as pd

from neo.ingest.gate import GateConfig, gate, read_all

CUTOFF = pd.Timestamp("2026-11-12 03:00", tz="UTC")


def _samples(tag: str, values, quality="Good") -> pd.DataFrame:
    ts = pd.date_range(end=CUTOFF, periods=len(values), freq="15min")
    return pd.DataFrame({"tag": tag, "ts_utc": ts, "value": values, "quality": quality})


def _flags(frame: pd.DataFrame, critical: set[str], digital: frozenset[str] = frozenset()):
    cfg = GateConfig()
    return {f.code for f in gate(read_all(frame, CUTOFF, cfg, digital=digital), critical, CUTOFF, cfg)}


def test_flat_analog_critical_tag_blocks():
    assert "critical_tag_stuck" in _flags(_samples("HUBS.PRESS", [24.0] * 16), {"HUBS.PRESS"})


def test_constant_digital_tag_is_not_stuck():
    tag = "HUB-C.T1.RUN"
    assert not _flags(_samples(tag, [1.0] * 16), {tag}, digital=frozenset({tag}))


def test_bad_quality_critical_tag_blocks():
    frame = _samples("HUBS.PRESS", [np.nan] * 16, quality="Bad")
    assert "critical_tag_bad" in _flags(frame, {"HUBS.PRESS"})


def test_noisy_good_tag_passes():
    rng = np.random.default_rng(0)
    assert not _flags(_samples("HUBS.PRESS", 24 + rng.normal(0, 0.05, 16)), {"HUBS.PRESS"})
