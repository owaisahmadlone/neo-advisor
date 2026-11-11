"""Steady-state wet-gas pipeline hydraulics in squared-pressure form.

  u_in - e^s u_out = g(q) = K q^2 + L / (1 + beta q)

* ``K q^2`` is the Weymouth friction term with a pipeline efficiency factor.
* ``L / (1 + beta q)`` is a Flanigan (1958) hydrostatic liquid-head term: the liquid
  holdup factor H_f = 1 / (1 + a V_sg) falls as gas velocity (proportional to q) rises.
  At turndown the liquid head grows, which a constant efficiency factor misses.

g(q) is convex for q >= 0, which is what lets the optimiser use conservative chords.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from neo.core.units import PSI_PER_BAR, STD_PRESSURE_PSIA, STD_TEMPERATURE_R, celsius_to_rankine, km_to_miles


def weymouth_coefficient(
    *,
    length_km: float,
    diameter_in: float,
    efficiency: float,
    gas_sg: float,
    t_avg_c: float,
    z_avg: float,
) -> float:
    """K in bara^2/(MMscfd)^2 from the field-unit Weymouth equation.

    Q[scf/d] = 433.5 E (Tb/Pb) [(P1^2 - P2^2) / (G T L Z)]^0.5 D^(8/3), P psia, L miles.
    """
    base = 433.5 * efficiency * (STD_TEMPERATURE_R / STD_PRESSURE_PSIA) * diameter_in ** (8 / 3)
    per_mmscfd = 1e6 / base
    k_psia = per_mmscfd**2 * gas_sg * celsius_to_rankine(t_avg_c) * km_to_miles(length_km) * z_avg
    return k_psia / PSI_PER_BAR**2


@dataclass(frozen=True, slots=True)
class PipeHydraulics:
    k_friction: float
    liquid_head: float = 0.0
    holdup_beta: float = 0.0
    elevation_s: float = 0.0

    def dp2(self, q: float) -> float:
        q = max(q, 0.0)
        return self.k_friction * q**2 + self.liquid_head / (1 + self.holdup_beta * q)

    @property
    def outlet_factor(self) -> float:
        return math.exp(self.elevation_s)

    def inlet_pressure(self, p_out: float, q: float) -> float:
        return math.sqrt(self.outlet_factor * p_out**2 + self.dp2(q))

    def outlet_pressure(self, p_in: float, q: float) -> float:
        residual = p_in**2 - self.dp2(q)
        return math.sqrt(residual / self.outlet_factor) if residual > 0 else math.nan


@dataclass(frozen=True, slots=True)
class HoldupModel:
    """Steady liquid inventory in a line: V * h_max / (1 + beta q) (bbl)."""

    line_volume_bbl: float
    holdup_max: float
    beta: float

    def inventory_bbl(self, q: float) -> float:
        return self.line_volume_bbl * self.holdup_max / (1 + self.beta * max(q, 0.0))

    def tangent(self, q0: float) -> tuple[float, float]:
        """(slope, intercept) of the tangent at q0; it under-estimates the convex inventory."""
        value = self.inventory_bbl(q0)
        slope = -self.line_volume_bbl * self.holdup_max * self.beta / (1 + self.beta * q0) ** 2
        return slope, value - slope * q0


def line_volume_bbl(*, length_km: float, diameter_in: float) -> float:
    diameter_m = diameter_in * 0.0254
    return math.pi / 4 * diameter_m**2 * length_km * 1000 * 6.28981
