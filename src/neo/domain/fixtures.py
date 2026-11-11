"""Build a ``NetworkState`` straight from the registry, for tests and the fixture CLI path.

A fixture is the registry's nominal engineering view plus a small set of overrides
(trains available, CO2 removal capacity, nominations, ...). It skips ingest and
calibration so the optimiser can be exercised on its own.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from neo.domain.registry import Registry
from neo.domain.state import NetworkState, OperatingPoint, WellState

DESIGN_MANIFOLD_BARA = {"P1": 40.0, "P2": 40.0, "P3": 80.0}


class FixtureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gas_day: date = date(2026, 11, 13)
    trains_available: int | None = None
    trains_running: int | None = None
    co2_removal_cap_mmscfd: float | None = None
    plant_inlet_cap_mmscfd: float | None = None
    nomination_fraction: dict[str, float] = Field(default_factory=dict)
    current_rate_fraction: float = 0.85
    suction_bara: float = 25.0
    ambient_c: float = 31.0
    third_party_rate_mmscfd: float | None = None
    condensate_headroom_bbld: float = 20_000.0
    wells_unavailable: list[str] = Field(default_factory=list)
    wells_offline: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> FixtureSpec:
        return cls.model_validate(yaml.safe_load(path.read_text()) or {})


def state_from_registry(registry: Registry, spec: FixtureSpec | None = None) -> NetworkState:
    spec = spec or FixtureSpec()
    net = registry.network
    calendar = net.calendar

    wells = {}
    for well in net.wells:
        available = well.id not in spec.wells_unavailable
        online = available and well.id not in spec.wells_offline
        design_rate = well.curve.rate(DESIGN_MANIFOLD_BARA[well.platform])
        rate = min(spec.current_rate_fraction * design_rate, well.rate_cap_mmscfd) if online else 0.0
        wells[well.id] = WellState(
            well_id=well.id,
            available=available,
            online=online,
            rate_mmscfd=rate,
            curve=well.curve,
            sigma_rel=0.04,
            test_age_days=12.0,
        )

    routes = {p.id: p.export_options[0] for p in net.platforms}
    trains_available = spec.trains_available if spec.trains_available is not None else net.compressor.trains
    trains_running = spec.trains_running if spec.trains_running is not None else trains_available
    nominations = {c.id: spec.nomination_fraction.get(c.id, 1.0) * c.dcq_mmbtu_d for c in registry.commercial.gsas}
    third_party = {
        s.id: spec.third_party_rate_mmscfd if spec.third_party_rate_mmscfd is not None else s.nominal_rate_mmscfd
        for s in net.third_party
    }
    return NetworkState(
        gas_day=spec.gas_day,
        revision=0,
        snapshot_cutoff_utc=calendar.nomination_cutoff_utc(spec.gas_day),
        wells=wells,
        current=OperatingPoint(routes=routes, trains_running=trains_running, suction_bara=spec.suction_bara),
        trains_available=trains_available,
        ambient_c=spec.ambient_c,
        third_party_rates=third_party,
        pipe_hydraulics={p.id: p.hydraulics for p in net.pipes},
        plant_inlet_cap_mmscfd=spec.plant_inlet_cap_mmscfd or net.plant.inlet_cap_mmscfd,
        co2_removal_cap_mmscfd=(
            spec.co2_removal_cap_mmscfd if spec.co2_removal_cap_mmscfd is not None else net.plant.co2_removal_cap_mmscfd
        ),
        condensate_headroom_bbld=spec.condensate_headroom_bbld,
        nominations_mmbtu_d=nominations,
    )
