"""Gas-quality blending and plant CO2 removal.

CO2 and H2S mole fractions, Hg concentration and ideal-gas gross heating value all
blend linearly on standard-volume (molar) flow (ISO 6976: GHV = sum x_i H_i). Wobbe
index and hydrocarbon dew point do not, so they are computed after blending.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Composition:
    co2: float  # mole fraction
    h2s_ppmv: float
    hg_ug_sm3: float
    ghv_btu_scf: float  # gross heating value of the raw stream, CO2 included as diluent


def blend(streams: Iterable[tuple[float, Composition]]) -> tuple[float, Composition]:
    """Total flow and molar-average composition of commingled streams."""
    streams = [(q, c) for q, c in streams if q > 0]
    total = sum(q for q, _ in streams)
    if total <= 0:
        return 0.0, Composition(0.0, 0.0, 0.0, 0.0)

    def mix(attr: str) -> float:
        return sum(q * getattr(c, attr) for q, c in streams) / total

    return total, Composition(mix("co2"), mix("h2s_ppmv"), mix("hg_ug_sm3"), mix("ghv_btu_scf"))


@dataclass(frozen=True, slots=True)
class SalesGas:
    volume_mmscfd: float
    energy_mmbtu_d: float
    co2: float

    @property
    def ghv_btu_scf(self) -> float:
        return self.energy_mmbtu_d / self.volume_mmscfd if self.volume_mmscfd > 0 else 0.0


def plant_outlet(*, q_in: float, inlet: Composition, co2_removed: float, shrink_frac: float) -> SalesGas:
    """Sales gas after shrinkage (NGL + plant fuel) and CO2 removal (volume in MMscfd)."""
    kept = 1 - shrink_frac
    volume = kept * q_in - co2_removed
    co2_volume = kept * q_in * inlet.co2 - co2_removed
    co2 = co2_volume / volume if volume > 0 else 0.0
    return SalesGas(volume, kept * q_in * inlet.ghv_btu_scf, co2)


def co2_removal_needed(*, q_in: float, inlet: Composition, co2_max: float, shrink_frac: float) -> float:
    """Smallest CO2 removal (MMscfd) that brings sales gas to ``co2_max``."""
    kept_q = (1 - shrink_frac) * q_in
    excess = kept_q * inlet.co2 - co2_max * kept_q
    return max(0.0, excess / (1 - co2_max))
