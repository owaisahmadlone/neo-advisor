"""Centrifugal compressor train: polytropic head, power, surge and stonewall limits.

Capacity of a train is the smaller of its driver-power limit and its stonewall
(choke) limit; both depend on suction and discharge pressure. That dependence is what
a flat "machines x capacity" model misses on trip days (Rios-Mercado & Borraz-Sanchez
2015).
"""

from __future__ import annotations

from dataclasses import dataclass

from neo.core.units import (
    GAS_CONSTANT,
    KW_TO_BTU_PER_H,
    actual_m3_s,
    celsius_to_kelvin,
    kg_s_to_mmscfd,
    mmscfd_to_kg_s,
)


@dataclass(frozen=True, slots=True)
class TrainMap:
    rated_power_kw: float
    design_ambient_c: float
    derate_per_c: float
    polytropic_eff: float
    k: float
    z: float
    molar_mass: float
    suction_temp_c: float
    stonewall_m3_s: float
    surge_m3_s: float
    ratio_max: float
    surge_margin: float = 0.10

    def available_power_kw(self, ambient_c: float) -> float:
        derate = max(0.0, ambient_c - self.design_ambient_c) * self.derate_per_c
        return self.rated_power_kw * max(0.0, 1 - derate)

    def head_kj_kg(self, ps: float, pd: float) -> float:
        exponent = (self.k - 1) / (self.k * self.polytropic_eff)
        zrt_m = self.z * GAS_CONSTANT * celsius_to_kelvin(self.suction_temp_c) / (self.molar_mass / 1000)
        ratio = max(pd / ps, 1.0)
        return zrt_m * (ratio**exponent - 1) / exponent / 1000

    def power_kw(self, q_mmscfd: float, ps: float, pd: float) -> float:
        return mmscfd_to_kg_s(q_mmscfd, self.molar_mass) * self.head_kj_kg(ps, pd) / self.polytropic_eff

    def stonewall_mmscfd(self, ps: float) -> float:
        per_unit = actual_m3_s(1.0, ps, self.suction_temp_c, self.z)
        return self.stonewall_m3_s / per_unit

    def surge_min_mmscfd(self, ps: float) -> float:
        per_unit = actual_m3_s(1.0, ps, self.suction_temp_c, self.z)
        return (1 + self.surge_margin) * self.surge_m3_s / per_unit

    def capacity_mmscfd(self, ps: float, pd: float, ambient_c: float) -> float:
        head = self.head_kj_kg(ps, pd)
        if head <= 0:
            return self.stonewall_mmscfd(ps)
        m_kg_s = self.available_power_kw(ambient_c) * self.polytropic_eff / head
        return min(kg_s_to_mmscfd(m_kg_s, self.molar_mass), self.stonewall_mmscfd(ps))


@dataclass(frozen=True, slots=True)
class FuelModel:
    thermal_eff: float
    lhv_btu_scf: float
    idle_power_kw: float = 0.0

    def fuel_mmscfd(self, shaft_power_kw: float) -> float:
        mmbtu_d = (shaft_power_kw + self.idle_power_kw) / self.thermal_eff * 24 * KW_TO_BTU_PER_H / 1e6
        return mmbtu_d / self.lhv_btu_scf


def station_fuel_mmscfd(
    train: TrainMap, fuel: FuelModel, *, q_mmscfd: float, trains: int, ps: float, pd: float
) -> float:
    """Fuel for ``trains`` identical trains sharing ``q`` equally."""
    if trains == 0:
        return 0.0
    per_train = train.power_kw(q_mmscfd / trains, ps, pd)
    return trains * fuel.fuel_mmscfd(per_train)
