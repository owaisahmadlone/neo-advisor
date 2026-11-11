"""Unit conventions and conversions.

Internal units (declared once, used everywhere):
    pressure      bara
    gas rate      MMscfd at standard conditions (60 degF, 14.696 psia)
    energy        MMBtu/d  (MMscfd x Btu/scf = MMBtu/d)
    heating value Btu/scf (gross, ideal-gas basis per ISO 6976)
    liquids       bbl/d; CGR and WGR in bbl/MMscf
    temperature   degC
    money         USD
"""

from __future__ import annotations

PSI_PER_BAR = 14.503773773
ATM_BARA = 1.01325
STD_TEMPERATURE_K = 288.706  # 60 degF
STD_PRESSURE_BARA = 1.01325  # 14.696 psia
STD_TEMPERATURE_R = 519.67
STD_PRESSURE_PSIA = 14.696
GAS_CONSTANT = 8.314462618  # J/(mol K)
SCF_PER_M3 = 35.3146667
KM_PER_MILE = 1.609344
KW_TO_BTU_PER_H = 3412.14163


def barg_to_bara(p_barg: float) -> float:
    return p_barg + ATM_BARA


def bara_to_barg(p_bara: float) -> float:
    return p_bara - ATM_BARA


def bara_to_psia(p_bara: float) -> float:
    return p_bara * PSI_PER_BAR


def psia_to_bara(p_psia: float) -> float:
    return p_psia / PSI_PER_BAR


def celsius_to_kelvin(t_c: float) -> float:
    return t_c + 273.15


def celsius_to_rankine(t_c: float) -> float:
    return (t_c + 273.15) * 1.8


def km_to_miles(length_km: float) -> float:
    return length_km / KM_PER_MILE


def std_gas_density_kg_per_scf(molar_mass_g_mol: float) -> float:
    """Ideal-gas density at standard conditions, per standard cubic foot."""
    rho_kg_m3 = (STD_PRESSURE_BARA * 1e5 * molar_mass_g_mol / 1000) / (GAS_CONSTANT * STD_TEMPERATURE_K)
    return rho_kg_m3 / SCF_PER_M3


def mmscfd_to_kg_s(q_mmscfd: float, molar_mass_g_mol: float) -> float:
    return q_mmscfd * 1e6 * std_gas_density_kg_per_scf(molar_mass_g_mol) / 86_400


def kg_s_to_mmscfd(m_kg_s: float, molar_mass_g_mol: float) -> float:
    return m_kg_s * 86_400 / (1e6 * std_gas_density_kg_per_scf(molar_mass_g_mol))


def actual_m3_s(q_mmscfd: float, p_bara: float, t_c: float, z: float) -> float:
    """Actual volumetric flow at line conditions for a standard gas rate."""
    q_std_m3_s = q_mmscfd * 1e6 / SCF_PER_M3 / 86_400
    return q_std_m3_s * (STD_PRESSURE_BARA / p_bara) * (celsius_to_kelvin(t_c) / STD_TEMPERATURE_K) * z


def energy_mmbtu_d(q_mmscfd: float, ghv_btu_scf: float) -> float:
    return q_mmscfd * ghv_btu_scf
