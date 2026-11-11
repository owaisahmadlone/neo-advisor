"""Hydrate formation curve and MEG inhibitor dosing.

* Hydrate curve fitted per stream as T_h = a + b ln(P), from an EOS offline. Katz
  gas-gravity charts are poor for high-CO2 gas (Sloan & Koh 2008), so the fit is per
  stream, not generic.
* Inhibitor dose by Hammerschmidt (1934): dT = K W / (M (100 - W)), K = 1297 (degC basis).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

HAMMERSCHMIDT_K_C = 1297.0
MEG_MOLAR_MASS = 62.07


@dataclass(frozen=True, slots=True)
class HydrateCurve:
    a: float
    b: float

    def temperature_c(self, p_bara: float) -> float:
        return self.a + self.b * math.log(p_bara)


def meg_weight_pct(subcooling_c: float) -> float:
    """Rich-MEG concentration (wt%) in the water phase for a required depression."""
    if subcooling_c <= 0:
        return 0.0
    return 100 * MEG_MOLAR_MASS * subcooling_c / (HAMMERSCHMIDT_K_C + MEG_MOLAR_MASS * subcooling_c)


def meg_injection_per_bbl_water(subcooling_c: float, *, lean_meg_wt: float = 0.9) -> float:
    """Barrels of lean MEG needed per barrel of produced water."""
    w = meg_weight_pct(subcooling_c) / 100
    if w <= 0:
        return 0.0
    if w >= lean_meg_wt:
        raise ValueError("required MEG concentration exceeds lean MEG strength")
    mass_ratio = w / (lean_meg_wt - w)
    return mass_ratio / 1.11  # lean MEG density relative to water


def required_subcooling_c(curve: HydrateCurve, *, p_bara: float, t_min_c: float, margin_c: float) -> float:
    return curve.temperature_c(p_bara) + margin_c - t_min_c
