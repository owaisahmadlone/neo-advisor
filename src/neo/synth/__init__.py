"""Synthetic truth plant and landing-zone generator (never imported by the advisor itself)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from neo.domain.registry import Registry
from neo.synth.extracts import LandingZoneWriter
from neo.synth.noise import NoiseSpec
from neo.synth.scenarios import SCENARIOS, Scenario
from neo.synth.truth import DayResult, TruthPlant


def generate(
    registry_dir: Path,
    out_dir: Path,
    *,
    start: date,
    days: int,
    scenario: str,
    seed: int,
    noise: NoiseSpec | None = None,
) -> None:
    """Run the truth plant open-loop on its initial set-points and write the landing zone."""
    plant = TruthPlant(Registry.load(registry_dir), seed=seed, scenario=Scenario.load(scenario), start=start)
    writer = LandingZoneWriter(registry_dir, out_dir, seed=seed, noise=noise)
    writer.write_static(plant)
    setpoints = plant.initial_setpoints()
    previous = None
    for offset in range(days):
        result = plant.operate(start + timedelta(days=offset), setpoints, previous_rates=previous)
        writer.write_day(result, plant)
        previous = result.end_rates


__all__ = ["SCENARIOS", "DayResult", "LandingZoneWriter", "NoiseSpec", "Scenario", "TruthPlant", "generate"]
