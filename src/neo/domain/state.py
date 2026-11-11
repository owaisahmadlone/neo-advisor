"""The advisor's view of the network at the snapshot cut-off.

Produced by ingest + calibration (or by a fixture for tests). Everything here is already
validated; downstream code trusts it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Literal

from neo.physics.pipes import PipeHydraulics
from neo.physics.wells import WellheadCurve

Severity = Literal["info", "warning", "critical"]


@dataclass(frozen=True, slots=True)
class DataFlag:
    code: str
    subject: str
    message: str
    severity: Severity = "warning"


@dataclass(frozen=True, slots=True)
class WellState:
    well_id: str
    available: bool
    online: bool
    rate_mmscfd: float
    curve: WellheadCurve
    sigma_rel: float
    test_age_days: float

    @property
    def confidence(self) -> Literal["high", "medium", "low"]:
        if self.sigma_rel <= 0.05:
            return "high"
        return "medium" if self.sigma_rel <= 0.10 else "low"


@dataclass(frozen=True, slots=True)
class OperatingPoint:
    """Discrete settings and the suction setpoint in force at the cut-off."""

    routes: Mapping[str, str]
    trains_running: int
    suction_bara: float


@dataclass(frozen=True, slots=True)
class NetworkState:
    gas_day: date
    revision: int
    snapshot_cutoff_utc: datetime
    wells: Mapping[str, WellState]
    current: OperatingPoint
    trains_available: int
    ambient_c: float
    third_party_rates: Mapping[str, float]
    pipe_hydraulics: Mapping[str, PipeHydraulics]
    plant_inlet_cap_mmscfd: float
    co2_removal_cap_mmscfd: float
    condensate_headroom_bbld: float
    nominations_mmbtu_d: Mapping[str, float]
    flags: tuple[DataFlag, ...] = field(default_factory=tuple)

    @property
    def blocking_flags(self) -> tuple[DataFlag, ...]:
        return tuple(f for f in self.flags if f.severity == "critical")

    def with_overrides(self, **changes: object) -> NetworkState:
        return replace(self, **changes)
