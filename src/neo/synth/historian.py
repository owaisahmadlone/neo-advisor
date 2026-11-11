"""PI-archive extract: 15-minute samples of every tag, with realistic instrument faults.

Each tag gets a persistent error (meter bias, VFM bias, pressure-transmitter drift) drawn
once per seed, plus white noise, dropouts, Bad samples and stuck readings per gas day.
A stuck sensor keeps reporting Good quality, as real ones do. Critical tags are never
stuck at random: the data gate blocks on them, and that case belongs to the ``badtag``
scenario, not to background noise.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from neo.ingest.schema import HISTORIAN_COLUMNS
from neo.synth.noise import NoiseSpec
from neo.synth.seeding import keyed_rng
from neo.synth.tags import TagSpec, period_values
from neo.synth.truth import DayResult

SAMPLE_PERIOD = timedelta(minutes=15)
BAD_INPUT = "Bad Input"

HISTORIAN_SCHEMA = pa.schema(
    [
        ("af_path", pa.string()),
        ("tag", pa.string()),
        ("ts_utc", pa.timestamp("us", tz="UTC")),
        ("value", pa.float64()),
        ("uom", pa.string()),
        ("quality", pa.string()),
        ("substituted", pa.bool_()),
        ("digital_state", pa.string()),
    ]
)


@dataclass(frozen=True, slots=True)
class TagError:
    """Persistent error of one instrument: relative bias and pressure drift per day."""

    bias: float
    drift_per_day: float


@dataclass(frozen=True, slots=True)
class BadWindow:
    tag: str
    start_utc: datetime
    end_utc: datetime


class Instrumentation:
    """Turns true period values into historian samples."""

    def __init__(self, tags: Iterable[TagSpec], *, seed: int, noise: NoiseSpec) -> None:
        self.tags = tuple(tags)
        self.seed = seed
        self.noise = noise
        self.errors = {t.tag: self._draw_error(t) for t in self.tags}

    def __repr__(self) -> str:
        return f"Instrumentation(tags={len(self.tags)}, seed={self.seed})"

    def _draw_error(self, spec: TagSpec) -> TagError:
        rng = keyed_rng(self.seed, "instrument", spec.tag)
        bias_sd = {"flow": self.noise.meter_bias_sd, "fiscal": self.noise.fiscal_bias_sd, "vfm": self.noise.vfm_bias_sd}
        bias = rng.normal(0, bias_sd.get(spec.kind, 0.0))
        drift = rng.normal(0, self.noise.pressure_drift_sd_bar_per_day) if spec.kind == "pressure" else 0.0
        return TagError(float(bias), float(drift))

    def day_samples(
        self, result: DayResult, *, epoch_utc: datetime, bad_windows: Iterable[BadWindow] = ()
    ) -> pd.DataFrame:
        """All samples of the gas day; ``epoch_utc`` is where pressure drift starts from zero."""
        start, end = result.periods[0].start_utc, result.periods[-1].end_utc
        times = pd.date_range(start, end, freq=SAMPLE_PERIOD, inclusive="left").as_unit("us")
        starts = pd.DatetimeIndex([p.start_utc for p in result.periods]).as_unit("us")
        period_index = np.searchsorted(starts.asi8, times.asi8, side="right") - 1
        values = [period_values(p) for p in result.periods]
        days = np.asarray((times - pd.Timestamp(epoch_utc)) / pd.Timedelta(days=1), dtype=float)
        windows = list(bad_windows)
        frames = [
            self._tag_samples(spec, times, days, np.array([values[i][spec.tag] for i in period_index]), result, windows)
            for spec in self.tags
        ]
        return pd.concat(frames, ignore_index=True)

    def _tag_samples(
        self,
        spec: TagSpec,
        times: pd.DatetimeIndex,
        days: np.ndarray,
        truth: np.ndarray,
        result: DayResult,
        windows: list[BadWindow],
    ) -> pd.DataFrame:
        noise = self.noise
        rng = keyed_rng(self.seed, "samples", spec.tag, result.gas_day)
        measured = self._measure(spec, truth, days, rng)
        count = len(times)
        if spec.analog and not spec.critical and rng.random() < noise.stuck_probability:
            first = int(rng.integers(0, count))
            length = int(rng.integers(noise.stuck_samples[0], noise.stuck_samples[1] + 1))
            measured[first : first + length] = measured[first]
        bad = rng.random(count) < noise.bad_fraction
        forced_bad = np.zeros(count, dtype=bool)
        for window in (w for w in windows if w.tag == spec.tag):
            forced_bad |= (times >= window.start_utc) & (times <= window.end_utc)
        bad |= forced_bad
        keep = (rng.random(count) >= noise.dropout_fraction) | forced_bad
        measured[bad] = np.nan
        frame = pd.DataFrame(
            {
                "af_path": spec.af_path,
                "tag": spec.tag,
                "ts_utc": times,
                "value": measured,
                "uom": spec.uom,
                "quality": np.where(bad, "Bad", "Good"),
                "substituted": False,
                "digital_state": np.where(bad, BAD_INPUT, ""),
            }
        )
        return frame.loc[keep]

    def _measure(self, spec: TagSpec, truth: np.ndarray, days: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        noise, error = self.noise, self.errors[spec.tag]
        white = rng.standard_normal(len(truth))
        match spec.kind:
            case "pressure":
                return truth + error.drift_per_day * days + noise.pressure_sd_bar * white
            case "temperature":
                return truth + noise.temperature_sd_c * white
            case "flow":
                return truth * (1 + error.bias) * (1 + noise.meter_sd * white)
            case "fiscal":
                return truth * (1 + error.bias) * (1 + noise.fiscal_sd * white)
            case "vfm":
                return truth * (1 + error.bias) * (1 + noise.vfm_sd * white)
            case "analyser":
                return truth + noise.analyser_sd_mol_pct * white
            case "heating_value":
                return truth * (1 + noise.ghv_sd * white)
            case "choke":
                return np.clip(truth + noise.choke_sd_pct * white * (truth > 0), 0.0, 100.0)
            case "digital":
                return truth.astype(float)
            case _:
                raise ValueError(f"unknown tag kind {spec.kind!r}")

    def vfm_bias(self, tag: str) -> float:
        return self.errors[tag].bias


def append_historian(directory: Path, samples: pd.DataFrame) -> None:
    """Merge samples into one parquet file per UTC calendar date, sorted by tag then time."""
    directory.mkdir(parents=True, exist_ok=True)
    for day, part in samples.groupby(samples["ts_utc"].dt.date, sort=True):
        path = directory / f"{day.isoformat()}.parquet"
        merged = pd.concat([pd.read_parquet(path), part], ignore_index=True) if path.exists() else part
        merged = (
            merged.drop_duplicates(["tag", "ts_utc"], keep="last")
            .sort_values(["tag", "ts_utc"], kind="stable")
            .reset_index(drop=True)
        )
        table = pa.Table.from_pandas(merged[list(HISTORIAN_COLUMNS)], schema=HISTORIAN_SCHEMA, preserve_index=False)
        pq.write_table(table, path)


def reset_historian(directory: Path) -> None:
    for path in directory.glob("*.parquet"):
        path.unlink()
