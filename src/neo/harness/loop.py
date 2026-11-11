"""Closed-loop evaluation: truth plant -> landing zone -> advisor -> operator -> truth plant.

The advisor's set-points drive the loop. On each gas day the two baselines are replayed on
the same truth plant from the same starting rates (the plant has no hidden state), which
makes the comparison paired: same reservoir, same events, same nominations.
"""

from __future__ import annotations

import csv
import shutil
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from neo.advise import AdviseConfig, advise
from neo.domain.registry import Registry
from neo.harness.baselines import greedy, hold
from neo.harness.metrics import DayRecord, PolicyDay, summary_markdown
from neo.harness.operator import advisor_setpoints, highest_co2_first, operate_guarded
from neo.ingest.landing import LandingZone
from neo.ingest.state_builder import build_state
from neo.synth import LandingZoneWriter, Scenario, TruthPlant

MARKER = ".neo-harness"
HARNESS_ADVISE = AdviseConfig(value_samples=0, contributions=False)


def _fresh_dir(path: Path) -> Path:
    """Reuse a directory only if the harness created it."""
    if path.exists():
        if not (path / MARKER).exists():
            raise FileExistsError(f"{path} exists and was not created by the harness; choose another --out")
        shutil.rmtree(path)
    path.mkdir(parents=True)
    (path / MARKER).write_text("created by neo harness\n")
    return path


def run_scenario(
    registry_dir: Path,
    out_dir: Path,
    scenario: str,
    *,
    days: int,
    start: date,
    seed: int,
    cfg: AdviseConfig = HARNESS_ADVISE,
    progress: Callable[[str, int, int], None] | None = None,
) -> list[DayRecord]:
    registry = Registry.load(registry_dir)
    plant = TruthPlant(registry, seed=seed, scenario=Scenario.load(scenario), start=start)
    landing = _fresh_dir(out_dir / scenario) / "landing"
    writer = LandingZoneWriter(registry_dir, landing, seed=seed)
    writer.write_static(plant)

    first = plant.operate(start, plant.initial_setpoints())
    writer.write_day(first, plant)
    previous = first.end_rates
    records = []
    for offset in range(1, days + 1):
        gas_day = start + timedelta(days=offset)
        zone = LandingZone(landing)
        state = build_state(zone, gas_day)
        started = time.perf_counter()
        bundle = advise(zone.registry, state, cfg=cfg)
        seconds = time.perf_counter() - started
        reg = zone.registry
        if bundle.plan is not None:
            advisor = advisor_setpoints(reg, bundle.plan, bundle.advisory)
            advisor_order = list(bundle.advisory.choke_back_order)
        else:
            advisor = hold(state)
            advisor_order = highest_co2_first(reg, advisor)
        policies = {
            "advisor": (advisor, advisor_order),
            "hold": (held := hold(state), highest_co2_first(reg, held)),
            "greedy": (planned := greedy(reg, state), highest_co2_first(reg, planned)),
        }
        min_rates = {w.id: w.min_rate_mmscfd for w in reg.network.wells}
        results = {
            name: operate_guarded(plant, gas_day, sp, order, min_rates=min_rates, previous_rates=previous)[0]
            for name, (sp, order) in policies.items()
        }
        records.append(
            DayRecord(
                scenario=scenario,
                gas_day=gas_day,
                advisory_status=bundle.advisory.status,
                advise_seconds=seconds,
                policies={
                    name: PolicyDay(
                        value_usd=r.valuation.value,
                        shortfall_mmbtu=r.valuation.shortfall_total,
                        hard_violations=tuple(sorted({v.limit for v in r.violations if v.severity == "hard"})),
                    )
                    for name, r in results.items()
                },
            )
        )
        writer.write_day(results["advisor"], plant)
        previous = results["advisor"].end_rates
        if progress is not None:
            progress(scenario, offset, days)
    _write_records(out_dir / scenario / "days.csv", records)
    return records


def _write_records(path: Path, records: list[DayRecord]) -> None:
    rows = [r.row() for r in records]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run_harness(
    registry_dir: Path,
    out_dir: Path,
    *,
    scenarios: list[str],
    days: int,
    start: date,
    seed: int,
    progress: Callable[[str, int, int], None] | None = None,
) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    by_scenario = {
        name: run_scenario(registry_dir, out_dir, name, days=days, start=start, seed=seed, progress=progress)
        for name in scenarios
    }
    summary = summary_markdown(by_scenario)
    (out_dir / "summary.md").write_text(summary)
    return summary
