"""Plain-language wording for limits in client-visible messages.

Advisory text names the limit and the plant's own numbers, never optimiser row names,
chord indices or internal rule strings.
"""

from __future__ import annotations

from neo.verify.forward import Violation

LIMITS: dict[str, tuple[str, str]] = {
    "well_deliverability": ("well rate above what the well can deliver", "MMscfd"),
    "choke_hydrate_margin": ("hydrate margin downstream of the choke", "°C"),
    "maop": ("maximum allowable operating pressure", "bara"),
    "pipe_capacity": ("line capacity", "MMscfd"),
    "compressor_capacity": ("hub compression capacity", "MMscfd"),
    "compressor_surge": ("compressor minimum stable flow", "MMscfd"),
    "compressor_ratio": ("compressor pressure ratio", ""),
    "discharge_pressure": ("compressor discharge pressure limit", "bara"),
    "suction_pressure": ("compressor suction pressure floor", "bara"),
    "plant_inlet": ("gas plant inlet capacity", "MMscfd"),
    "co2_removal": ("CO2 removal needed", "MMscfd"),
    "sales_co2": ("sales-gas CO2 content", "mole fraction"),
    "sales_ghv_min": ("sales-gas heating value (minimum)", "Btu/scf"),
    "sales_ghv_max": ("sales-gas heating value (maximum)", "Btu/scf"),
    "h2s_inlet": ("H2S at the plant inlet", "ppmv"),
    "hg_inlet": ("mercury at the plant inlet", "µg/Sm³"),
    "slug_liquid": ("slug-catcher liquid handling", "bbl/d"),
    "terminal_headroom": ("condensate terminal headroom", "bbl/d"),
    "platform_water": ("platform water handling", "bbl/d"),
    "meg": ("MEG injection needed", "bbl/d"),
    "slug_sweep": ("liquid swept into the slug catcher", "bbl"),
    "move_limit": ("step limit from today's rate", "MMscfd"),
}

ROW_KINDS: dict[str, str] = {
    "pipe_dp": "pressure drop along a line",
    "pipe_capacity": "line capacity",
    "compressor_capacity": "hub compression capacity",
    "compressor_surge": "compressor minimum stable flow",
    "compressor_ratio": "compressor pressure ratio",
    "pd_max": "compressor discharge pressure limit",
    "plant_inlet": "gas plant inlet capacity",
    "co2_removal": "CO2 removal capacity",
    "sales_co2": "sales-gas CO2 specification",
    "sales_ghv": "sales-gas heating-value specification",
    "h2s_inlet": "H2S limit at the plant inlet",
    "hg_inlet": "mercury limit at the plant inlet",
    "slug_liquid": "slug-catcher liquid handling",
    "terminal_headroom": "condensate terminal headroom",
    "platform_water": "platform water handling",
    "meg": "MEG injection capacity",
    "slug_sweep": "slug-catcher room for swept liquid",
    "energy_balance": "sales-gas outlets",
}


def limit_label(limit: str) -> str:
    return LIMITS.get(limit, (limit.replace("_", " "), ""))[0]


def describe_violation(v: Violation) -> str:
    label, unit = LIMITS.get(v.limit, (v.limit.replace("_", " "), ""))
    unit = f" {unit}" if unit else ""
    return f"{label}: {v.value:,.2f}{unit} against a limit of {v.bound:,.2f}{unit}"


def row_kind_label(kind: str) -> str:
    return ROW_KINDS.get(kind, kind.replace("_", " "))
