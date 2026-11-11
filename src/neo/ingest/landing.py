"""Read and schema-check the landing zone (validate at the boundary, trust afterwards)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import cached_property
from pathlib import Path

import pandas as pd

from neo.core.ids import tree_hashes
from neo.domain.registry import Registry
from neo.ingest import schema


class LandingZoneError(ValueError):
    pass


def _read_csv(path: Path, columns: tuple[str, ...], *, required: bool = True) -> pd.DataFrame:
    if not path.exists():
        if required:
            raise LandingZoneError(f"missing extract {path.name}")
        return pd.DataFrame(columns=list(columns))
    frame = pd.read_csv(path)
    missing = set(columns) - set(frame.columns)
    if missing:
        raise LandingZoneError(f"{path.name}: missing columns {sorted(missing)}")
    return frame


def _utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


@dataclass(frozen=True)
class LandingZone:
    root: Path

    @cached_property
    def registry(self) -> Registry:
        return Registry.load(self.root / schema.REGISTERS_DIR)

    @cached_property
    def tag_map(self) -> pd.DataFrame:
        frame = _read_csv(self.root / "tag_map.csv", schema.TAG_MAP_COLUMNS)
        frame["critical"] = frame["critical"].astype(str).str.lower().isin(("true", "1"))
        return frame

    @cached_property
    def critical_tags(self) -> frozenset[str]:
        return frozenset(self.tag_map.loc[self.tag_map["critical"], "tag"])

    @cached_property
    def digital_tags(self) -> frozenset[str]:
        return frozenset(self.tag_map.loc[self.tag_map["uom"].astype(str).str.lower() == "bool", "tag"])

    @cached_property
    def well_tests(self) -> pd.DataFrame:
        frame = _read_csv(self.root / "well_tests.csv", schema.WELL_TEST_COLUMNS)
        frame["test_start_utc"] = _utc(frame["test_start_utc"])
        frame["accepted"] = frame["accepted"].astype(str).str.lower().isin(("true", "1"))
        return frame

    @cached_property
    def lab_samples(self) -> pd.DataFrame:
        frame = _read_csv(self.root / "lab_samples.csv", schema.LAB_SAMPLE_COLUMNS, required=False)
        frame["sampled_utc"] = _utc(frame["sampled_utc"])
        return frame

    @cached_property
    def events(self) -> pd.DataFrame:
        frame = _read_csv(self.root / "events.csv", schema.EVENT_COLUMNS, required=False)
        frame["start_utc"] = _utc(frame["start_utc"])
        frame["end_utc"] = _utc(frame["end_utc"])
        return frame

    @cached_property
    def nominations(self) -> pd.DataFrame:
        frame = _read_csv(self.root / "nominations.csv", schema.NOMINATION_COLUMNS)
        frame["submitted_utc"] = _utc(frame["submitted_utc"])
        frame["gas_day"] = pd.to_datetime(frame["gas_day"]).dt.date
        return frame

    @cached_property
    def third_party_schedule(self) -> pd.DataFrame:
        frame = _read_csv(self.root / "third_party_schedule.csv", schema.THIRD_PARTY_COLUMNS, required=False)
        frame["gas_day"] = pd.to_datetime(frame["gas_day"]).dt.date
        return frame

    @cached_property
    def terminal_plan(self) -> pd.DataFrame:
        frame = _read_csv(self.root / "terminal_plan.csv", schema.TERMINAL_COLUMNS, required=False)
        frame["gas_day"] = pd.to_datetime(frame["gas_day"]).dt.date
        return frame

    @cached_property
    def dor_allocation(self) -> pd.DataFrame:
        frame = _read_csv(self.root / "dor_allocation.csv", schema.DOR_COLUMNS, required=False)
        frame["gas_day"] = pd.to_datetime(frame["gas_day"]).dt.date
        return frame

    def historian(self, start: datetime, end: datetime) -> pd.DataFrame:
        """Samples with start <= ts_utc <= end, from the per-UTC-day parquet files."""
        directory = self.root / schema.HISTORIAN_DIR
        days = pd.date_range(start.date() - timedelta(days=1), end.date(), freq="D")
        frames = [pd.read_parquet(p) for d in days if (p := directory / f"{d.date().isoformat()}.parquet").exists()]
        if not frames:
            return pd.DataFrame(columns=list(schema.HISTORIAN_COLUMNS))
        frame = pd.concat(frames, ignore_index=True)
        missing = set(schema.HISTORIAN_COLUMNS) - set(frame.columns)
        if missing:
            raise LandingZoneError(f"historian: missing columns {sorted(missing)}")
        frame["ts_utc"] = pd.to_datetime(frame["ts_utc"], utc=True)
        window = (frame["ts_utc"] >= pd.Timestamp(start)) & (frame["ts_utc"] <= pd.Timestamp(end))
        return frame.loc[window].sort_values(["tag", "ts_utc"], kind="stable").reset_index(drop=True)

    def input_hashes(self, *, up_to: date | None = None) -> dict[str, str]:
        """Hashes of every extract file (historian files after ``up_to`` excluded)."""
        hashes = tree_hashes(self.root)
        if up_to is None:
            return hashes
        prefix = f"{schema.HISTORIAN_DIR}/"
        return {
            path: digest
            for path, digest in hashes.items()
            if not path.startswith(prefix) or path.removeprefix(prefix)[:10] <= up_to.isoformat()
        }
