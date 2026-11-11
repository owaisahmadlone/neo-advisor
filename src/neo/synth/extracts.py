"""Landing-zone writer: turns truth into the extracts source systems would deliver.

The layout and columns follow ``neo.ingest.schema`` exactly. Information appears when a
real system would publish it: events when they start, nominations and day-ahead plans at
10:00 local the day before, allocations one day late. Every file is sorted and carries
no wall-clock time, so a seed always gives byte-identical output.
"""

from __future__ import annotations

import math
import shutil
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from neo.core.units import bara_to_barg
from neo.domain.registry import Registry
from neo.ingest import schema
from neo.synth.historian import BadWindow, Instrumentation, append_historian, reset_historian
from neo.synth.noise import NoiseSpec
from neo.synth.seeding import keyed_rng
from neo.synth.tags import TagSpec, choke_pct, flowing_wellhead_bara, tag_catalogue
from neo.synth.truth import DayResult, PeriodResult, TruthPlant

REGISTER_FILES = ("asset_registry.yaml", "contracts.yaml", "psc_terms.yaml")
TAG_MAP_FILE = "tag_map.csv"
TEST_INTERVAL_DAYS = 30
TEST_START_LOCAL = time(12)
TEST_DURATIONS_H = (6, 8, 12)
DAY_AHEAD_SUBMIT_LOCAL = time(10)
LAB_LEAD_DAYS = 45
LAB_SAMPLE_LOCAL = time(10)

EXTRACTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "well_tests.csv": (schema.WELL_TEST_COLUMNS, ("well_id", "test_start_utc")),
    "lab_samples.csv": (schema.LAB_SAMPLE_COLUMNS, ("sample_point", "sampled_utc")),
    "events.csv": (schema.EVENT_COLUMNS, ("event_id",)),
    "nominations.csv": (schema.NOMINATION_COLUMNS, ("gas_day", "contract_id", "revision")),
    "prices.csv": (schema.PRICE_COLUMNS, ("gas_day",)),
    "third_party_schedule.csv": (schema.THIRD_PARTY_COLUMNS, ("gas_day", "stream_id")),
    "terminal_plan.csv": (schema.TERMINAL_COLUMNS, ("gas_day",)),
    "dor_allocation.csv": (schema.DOR_COLUMNS, ("gas_day", "well_id")),
}

Row = dict[str, object]


def _fmt(value: object) -> str:
    match value:
        case None:
            return ""
        case bool():
            return str(value)
        case float() | np.floating() if math.isnan(value):
            return ""
        case float() | np.floating():
            return repr(round(float(value), 4))
        case datetime():
            return value.astimezone(UTC).isoformat()
        case date():
            return value.isoformat()
        case _:
            return str(value)


def _write_csv(path: Path, columns: tuple[str, ...], frame: pd.DataFrame) -> None:
    frame[list(columns)].to_csv(path, index=False, lineterminator="\n")


def _merge_csv(path: Path, columns: tuple[str, ...], keys: tuple[str, ...], rows: Iterable[Row]) -> None:
    """Add rows to an extract; a re-written key replaces the old row, output stays sorted."""
    new = pd.DataFrame([{c: _fmt(r[c]) for c in columns} for r in rows], columns=list(columns), dtype=str)
    if new.empty:
        return
    old = pd.read_csv(path, dtype=str, keep_default_na=False)
    merged = (
        pd.concat([old, new], ignore_index=True)
        .drop_duplicates(list(keys), keep="last")
        .sort_values(list(keys), kind="stable")
    )
    _write_csv(path, columns, merged)


def _local(calendar_day: date, clock: time, plant: TruthPlant) -> datetime:
    return datetime.combine(calendar_day, clock, tzinfo=plant.calendar.zone).astimezone(UTC)


class LandingZoneWriter:
    """Writes one landing zone. Use one writer per run: the lagged allocation is held between days."""

    def __init__(self, registry_dir: Path, out_dir: Path, *, seed: int, noise: NoiseSpec | None = None) -> None:
        self.registry_dir = registry_dir
        self.out_dir = out_dir
        self.seed = seed
        self.noise = noise or NoiseSpec()
        self.registry = Registry.load(registry_dir)
        self.tags: tuple[TagSpec, ...] = tag_catalogue(self.registry.network)
        self.instruments = Instrumentation(self.tags, seed=seed, noise=self.noise)
        self._pending_dor: dict[date, list[Row]] = {}

    def __repr__(self) -> str:
        return f"LandingZoneWriter(out_dir={self.out_dir!s}, seed={self.seed})"

    @property
    def historian_dir(self) -> Path:
        return self.out_dir / schema.HISTORIAN_DIR

    # ------------------------------------------------------------------ public API
    def write_static(self, plant: TruthPlant) -> None:
        """Start a fresh landing zone: registers, tag map and everything dated before the run."""
        self._reset()
        registers = self.out_dir / schema.REGISTERS_DIR
        for name in REGISTER_FILES:
            shutil.copyfile(self.registry_dir / name, registers / name)
        _write_csv(self.out_dir / TAG_MAP_FILE, schema.TAG_MAP_COLUMNS, self._tag_map())
        self._merge("well_tests.csv", self._backfilled_tests(plant))
        self._merge("lab_samples.csv", self._lab_samples(plant))
        run_start = plant.calendar.bounds_utc(plant.start)[0]
        self._merge("events.csv", self._event_rows(plant, until=run_start))
        self._write_day_ahead(plant, plant.start)

    def write_day(self, result: DayResult, plant: TruthPlant) -> None:
        """Everything source systems publish during ``result.gas_day``."""
        day_start, day_end = plant.calendar.bounds_utc(result.gas_day)
        samples = self.instruments.day_samples(
            result, epoch_utc=plant.calendar.bounds_utc(plant.start)[0], bad_windows=self._bad_windows(plant)
        )
        append_historian(self.historian_dir, samples)
        self._merge("events.csv", self._event_rows(plant, since=day_start, until=day_end))
        self._merge("well_tests.csv", self._scheduled_tests(result, plant))
        self._write_day_ahead(plant, result.gas_day + timedelta(days=1))
        due = sorted(d for d in self._pending_dor if d < result.gas_day)
        self._merge("dor_allocation.csv", [row for d in due for row in self._pending_dor.pop(d)])
        self._pending_dor[result.gas_day] = self._dor_rows(result)

    # ------------------------------------------------------------------ files
    def _reset(self) -> None:
        (self.out_dir / schema.REGISTERS_DIR).mkdir(parents=True, exist_ok=True)
        self.historian_dir.mkdir(parents=True, exist_ok=True)
        reset_historian(self.historian_dir)
        for name, (columns, _) in EXTRACTS.items():
            _write_csv(self.out_dir / name, columns, pd.DataFrame(columns=list(columns)))
        self._pending_dor.clear()

    def _merge(self, name: str, rows: Iterable[Row]) -> None:
        columns, keys = EXTRACTS[name]
        _merge_csv(self.out_dir / name, columns, keys, rows)

    def _tag_map(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"tag": t.tag, "asset_id": t.asset_id, "attribute": t.attribute, "uom": t.uom, "critical": t.critical}
                for t in self.tags
            ]
        )

    def _bad_windows(self, plant: TruthPlant) -> list[BadWindow]:
        return [BadWindow(b.tag, *b.window_utc(plant.calendar, plant.start)) for b in plant.scenario.bad_tags]

    # ------------------------------------------------------------------ well tests
    def _test_phase(self) -> dict[str, int]:
        """Stagger tests so the wells' monthly tests are spread across the month."""
        wells = sorted(w.id for w in self.registry.network.wells)
        return {w: i * TEST_INTERVAL_DAYS // len(wells) for i, w in enumerate(wells)}

    def _backfilled_tests(self, plant: TruthPlant) -> list[Row]:
        """The latest accepted test before the run, taken while the plant ran on its initial set-points."""
        rows = []
        setpoints = plant.initial_setpoints()
        for well_id, phase in self._test_phase().items():
            gas_day = plant.start + timedelta(days=phase - TEST_INTERVAL_DAYS)
            result = plant.operate(gas_day, setpoints)
            rows += self._tests_on(result, plant, [well_id], force_accept=True)
        return rows

    def _scheduled_tests(self, result: DayResult, plant: TruthPlant) -> list[Row]:
        offset = (result.gas_day - plant.start).days % TEST_INTERVAL_DAYS
        due = [w for w, phase in self._test_phase().items() if phase == offset]
        return self._tests_on(result, plant, due, force_accept=False)

    def _tests_on(self, result: DayResult, plant: TruthPlant, wells: list[str], *, force_accept: bool) -> list[Row]:
        rows = []
        for well_id in wells:
            rng = keyed_rng(self.seed, "well_test", well_id, result.gas_day)
            start = _local(result.gas_day, TEST_START_LOCAL, plant) + timedelta(minutes=15 * int(rng.integers(0, 9)))
            row = self._test_row(well_id, result.period_at(start), start, rng, force_accept=force_accept)
            if row is not None:
                rows.append(row)
        return rows

    def _test_row(
        self, well_id: str, period: PeriodResult, start: datetime, rng: np.random.Generator, *, force_accept: bool
    ) -> Row | None:
        noise = self.noise
        well, physics = period.outcome.wells[well_id], period.physical.wells[well_id]
        if well.rate <= 0:
            return None
        accepted = bool(rng.random() >= noise.test_reject_fraction) or force_accept
        spread = 1.0 if accepted else 3.0
        gas, cond, water, pwh, co2 = rng.standard_normal(5) * spread
        return {
            "well_id": well_id,
            "test_start_utc": start,
            "duration_h": float(rng.choice(TEST_DURATIONS_H)),
            "q_gas_mmscfd": well.rate * (1 + noise.test_gas_sd * gas),
            "q_cond_bbld": well.rate * physics.cgr * (1 + noise.test_liquid_sd * cond),
            "q_water_bbld": well.rate * physics.wgr * (1 + noise.test_liquid_sd * water),
            "pwh_barg": bara_to_barg(flowing_wellhead_bara(well)) + noise.test_pwh_sd_bar * pwh,
            "choke_pct": choke_pct(well),
            "co2_mol_pct": 100 * physics.composition.co2 + noise.test_co2_sd_mol_pct * co2,
            "accepted": accepted,
        }

    # ------------------------------------------------------------------ lab samples
    def _lab_samples(self, plant: TruthPlant) -> list[Row]:
        net = self.registry.network
        truth = {w_id: w.composition for w_id, w in plant.wells.items()} | {
            s.id: s.composition for s in net.third_party
        }
        rows = []
        for index, (point, comp) in enumerate(sorted(truth.items())):
            rng = keyed_rng(self.seed, "lab", point)
            sampled = _local(plant.start - timedelta(days=LAB_LEAD_DAYS - index % 15), LAB_SAMPLE_LOCAL, plant)
            co2, h2s, hg, ghv = rng.standard_normal(4)
            rows.append(
                {
                    "sample_point": point,
                    "sampled_utc": sampled,
                    "co2_mol_pct": 100 * comp.co2 + self.noise.lab_co2_sd_mol_pct * co2,
                    "h2s_ppmv": comp.h2s_ppmv * (1 + self.noise.lab_rel_sd * h2s),
                    "hg_ug_sm3": comp.hg_ug_sm3 * (1 + self.noise.lab_rel_sd * hg),
                    "ghv_btu_scf": comp.ghv_btu_scf * (1 + self.noise.lab_ghv_sd * ghv),
                }
            )
        return rows

    # ------------------------------------------------------------------ events and day-ahead
    def _event_rows(self, plant: TruthPlant, *, until: datetime, since: datetime | None = None) -> list[Row]:
        return [
            {
                "event_id": e.event_id,
                "asset_id": e.asset_id,
                "type": e.type,
                "start_utc": e.start_utc,
                "end_utc": e.end_utc,
                "capacity_removed": float(e.capacity_removed),
                "note": e.note,
            }
            for e in plant.events
            if e.start_utc < until and (since is None or e.start_utc >= since)
        ]

    def _write_day_ahead(self, plant: TruthPlant, gas_day: date) -> None:
        """Nominations, prices, PAC declaration and terminal plan for ``gas_day``, issued the day before."""
        conditions = plant.conditions(gas_day)
        submitted = _local(gas_day - timedelta(days=1), DAY_AHEAD_SUBMIT_LOCAL, plant)
        commercial = self.registry.commercial
        self._merge(
            "nominations.csv",
            [
                {"gas_day": gas_day, "contract_id": c, "revision": 0, "qty_mmbtu_d": q, "submitted_utc": submitted}
                for c, q in sorted(conditions.nominations.items())
            ],
        )
        lng = max((o.price_usd_mmbtu for o in commercial.excess_outlets), default=math.nan)
        self._merge(
            "prices.csv",
            [{"gas_day": gas_day, "condensate_usd_bbl": commercial.condensate_usd_bbl, "lng_netback_usd_mmbtu": lng}],
        )
        self._merge("third_party_schedule.csv", self._third_party_rows(gas_day, conditions.third_party_rates))
        self._merge(
            "terminal_plan.csv", [{"gas_day": gas_day, "receiving_headroom_bbld": conditions.terminal_headroom_bbld}]
        )

    def _third_party_rows(self, gas_day: date, true_rates: Mapping[str, float]) -> list[Row]:
        swing = self.noise.third_party_declared_swing
        rows = []
        for stream_id, rate in sorted(true_rates.items()):
            rng = keyed_rng(self.seed, "third_party", stream_id, gas_day)
            declared = rate * (1 + rng.uniform(-swing, swing))
            if rng.random() >= self.noise.third_party_missing_fraction:
                rows.append({"gas_day": gas_day, "stream_id": stream_id, "rate_mmscfd": declared, "source": "declared"})
        return rows

    # ------------------------------------------------------------------ allocation
    def _dor_rows(self, result: DayResult) -> list[Row]:
        """Fiscal totals allocated back to wells pro rata to their VFM rates (gas) and test ratios (liquids)."""
        net = self.registry.network
        rates = result.well_rates
        rng = keyed_rng(self.seed, "dor", result.gas_day)
        fiscal_error, cond_error, water_error = rng.standard_normal(3)
        vfm = {w: q * (1 + self.instruments.vfm_bias(schema.well_tag(w, "VFM_GAS"))) for w, q in sorted(rates.items())}
        total_vfm = sum(vfm.values())
        physics = result.periods[-1].physical.wells
        gas = sum(rates.values()) * (1 + self.noise.fiscal_sd * fiscal_error)
        cond = sum(result.daily.our_condensate_by_well.values()) * (1 + self.noise.fiscal_sd * cond_error)
        water = sum(q * physics[w].wgr for w, q in rates.items()) * (1 + self.noise.water_meter_sd * water_error)
        cond_weight = {w: v * net.well_by_id[w].cgr_bbl_mmscf for w, v in vfm.items()}
        water_weight = {w: v * net.well_by_id[w].wgr_bbl_mmscf for w, v in vfm.items()}

        def share(weights: Mapping[str, float], well_id: str, total: float) -> float:
            denominator = sum(weights.values())
            return total * weights[well_id] / denominator if denominator > 0 else 0.0

        return [
            {
                "gas_day": result.gas_day,
                "well_id": w,
                "gas_mmscfd": gas * vfm[w] / total_vfm if total_vfm > 0 else 0.0,
                "cond_bbld": share(cond_weight, w, cond),
                "water_bbld": share(water_weight, w, water),
                "method": "pro_rata_vfm",
            }
            for w in vfm
        ]
