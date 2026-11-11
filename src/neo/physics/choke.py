"""Isentropic-nozzle gas choke: translate a recommended rate into an opening.

  m = Cd A p_up sqrt( 2k/(k-1) * M/(Z R T) * (r^(2/k) - r^((k+1)/k)) ),
  r = max(p_dn/p_up, r_crit), r_crit = (2/(k+1))^(k/(k-1)).

Used only after the solve, for set-point translation and the Joule-Thomson check; the
optimiser treats the choke as a free pressure absorber.
"""

from __future__ import annotations

import math

from neo.core.units import GAS_CONSTANT, celsius_to_kelvin, kg_s_to_mmscfd


def critical_ratio(k: float) -> float:
    return (2 / (k + 1)) ** (k / (k - 1))


def mass_flux_kg_s_m2(
    p_up: float, p_dn: float, *, t_up_c: float, k: float, z: float, molar_mass: float, cd: float
) -> float:
    r = max(p_dn / p_up, critical_ratio(k))
    term = r ** (2 / k) - r ** ((k + 1) / k)
    density_factor = (molar_mass / 1000) / (z * GAS_CONSTANT * celsius_to_kelvin(t_up_c))
    return cd * p_up * 1e5 * math.sqrt(2 * k / (k - 1) * density_factor * term)


def opening_pct(
    q_mmscfd: float,
    *,
    p_up: float,
    p_dn: float,
    max_area_m2: float,
    t_up_c: float,
    k: float = 1.28,
    z: float = 0.88,
    molar_mass: float = 19.0,
    cd: float = 0.85,
) -> float:
    """Opening (% of full bore area, linear trim) that passes ``q`` between the pressures."""
    if q_mmscfd <= 0:
        return 0.0
    if p_up <= p_dn:
        return math.inf
    flux = mass_flux_kg_s_m2(p_up, p_dn, t_up_c=t_up_c, k=k, z=z, molar_mass=molar_mass, cd=cd)
    per_full_bore = kg_s_to_mmscfd(flux * max_area_m2, molar_mass)
    return 100 * q_mmscfd / per_full_bore


def jt_downstream_temperature_c(t_up_c: float, dp_bar: float, *, jt_c_per_bar: float = 0.45) -> float:
    return t_up_c - jt_c_per_bar * max(dp_bar, 0.0)
