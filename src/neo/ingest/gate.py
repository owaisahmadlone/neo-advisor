"""Snapshot aggregation and the data gate.

Each tag is summarised over a window ending at the cut-off using only Good samples.
Critical tags that are Bad, stale or stuck block the run; other gaps are substituted and
flagged.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd

from neo.domain.state import DataFlag


@dataclass(frozen=True, slots=True)
class GateConfig:
    window_hours: float = 4.0
    stale_hours: float = 2.0
    stuck_samples: int = 12  # 3 h of identical 15-min values
    min_good_fraction: float = 0.5


@dataclass(frozen=True, slots=True)
class TagReading:
    tag: str
    mean: float | None
    last: float | None
    last_good_utc: pd.Timestamp | None
    good: int
    total: int
    stuck: bool

    @property
    def usable(self) -> bool:
        return self.mean is not None and not self.stuck


def read_tag(
    samples: pd.DataFrame, tag: str, cutoff: pd.Timestamp, cfg: GateConfig, *, digital: bool = False
) -> TagReading:
    """Digital (on/off) tags legitimately hold one value for days, so they are never 'stuck'."""
    rows = samples.loc[samples["tag"] == tag]
    total = len(rows)
    good = rows.loc[(rows["quality"] == "Good") & rows["value"].notna()]
    if good.empty:
        return TagReading(tag, None, None, None, 0, total, False)
    values = good["value"].to_numpy()
    tail = values[-cfg.stuck_samples :]
    stuck = not digital and len(tail) >= cfg.stuck_samples and float(tail.max() - tail.min()) == 0.0
    return TagReading(tag, float(values.mean()), float(values[-1]), good["ts_utc"].iloc[-1], len(good), total, stuck)


def gate(
    readings: dict[str, TagReading], critical: Iterable[str], cutoff: pd.Timestamp, cfg: GateConfig
) -> list[DataFlag]:
    flags = []
    stale_before = cutoff - pd.Timedelta(hours=cfg.stale_hours)
    for tag in sorted(critical):
        reading = readings.get(tag)
        if reading is None or reading.mean is None:
            flags.append(DataFlag("critical_tag_bad", tag, "no Good samples before the cut-off", "critical"))
        elif reading.last_good_utc < stale_before:
            flags.append(
                DataFlag(
                    "critical_tag_stale",
                    tag,
                    f"last Good sample {reading.last_good_utc:%Y-%m-%d %H:%M} UTC",
                    "critical",
                )
            )
        elif reading.stuck:
            flags.append(DataFlag("critical_tag_stuck", tag, "value has not changed for 3 h", "critical"))
        elif reading.total and reading.good / reading.total < cfg.min_good_fraction:
            flags.append(
                DataFlag("critical_tag_degraded", tag, f"{reading.good}/{reading.total} Good samples", "warning")
            )
    return flags


def read_all(
    samples: pd.DataFrame, cutoff: pd.Timestamp, cfg: GateConfig, *, digital: frozenset[str] = frozenset()
) -> dict[str, TagReading]:
    window = samples.loc[samples["ts_utc"] >= cutoff - pd.Timedelta(hours=cfg.window_hours)]
    return {tag: read_tag(window, tag, cutoff, cfg, digital=tag in digital) for tag in sorted(window["tag"].unique())}
