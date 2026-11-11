"""Landing-zone contract: file names, columns and tag conventions.

The synthetic generator writes this layout and the ingest stage reads it, so this module is
the single source of truth for both. It mirrors what source systems would deliver:

landing/
  registers/asset_registry.yaml, contracts.yaml, psc_terms.yaml   (copied registers)
  tag_map.csv                         PI tag <-> AF attribute
  historian/<YYYY-MM-DD>.parquet      PI archive extract, one file per calendar day (UTC)
  well_tests.csv                      MPFM / test-separator results
  lab_samples.csv                     PVT / analyser lab results
  events.csv                          FFN / ASOME / maintenance plan
  nominations.csv                     day-ahead nominations and renominations
  prices.csv                          commercial price assumptions per gas day
  third_party_schedule.csv            PAC declared rates (may be missing)
  terminal_plan.csv                   condensate terminal receiving headroom per gas day
  dor_allocation.csv                  lagged allocated actuals (verification only)
"""

from __future__ import annotations

from typing import Final, Literal

Quality = Literal["Good", "Questionable", "Bad"]

REGISTERS_DIR: Final = "registers"
HISTORIAN_DIR: Final = "historian"

HISTORIAN_COLUMNS: Final = ("af_path", "tag", "ts_utc", "value", "uom", "quality", "substituted", "digital_state")
TAG_MAP_COLUMNS: Final = ("tag", "asset_id", "attribute", "uom", "critical")
WELL_TEST_COLUMNS: Final = (
    "well_id",
    "test_start_utc",
    "duration_h",
    "q_gas_mmscfd",
    "q_cond_bbld",
    "q_water_bbld",
    "pwh_barg",
    "choke_pct",
    "co2_mol_pct",
    "accepted",
)
LAB_SAMPLE_COLUMNS: Final = ("sample_point", "sampled_utc", "co2_mol_pct", "h2s_ppmv", "hg_ug_sm3", "ghv_btu_scf")
EVENT_COLUMNS: Final = ("event_id", "asset_id", "type", "start_utc", "end_utc", "capacity_removed", "note")
NOMINATION_COLUMNS: Final = ("gas_day", "contract_id", "revision", "qty_mmbtu_d", "submitted_utc")
PRICE_COLUMNS: Final = ("gas_day", "condensate_usd_bbl", "lng_netback_usd_mmbtu")
THIRD_PARTY_COLUMNS: Final = ("gas_day", "stream_id", "rate_mmscfd", "source")
TERMINAL_COLUMNS: Final = ("gas_day", "receiving_headroom_bbld")
DOR_COLUMNS: Final = ("gas_day", "well_id", "gas_mmscfd", "cond_bbld", "water_bbld", "method")

# Event types the advisor understands.
EVENT_TYPES: Final = (
    "train_trip",
    "train_maintenance",
    "well_failure",
    "co2_removal_derate",
    "plant_derate",
    "pipe_restriction",
)


def well_tag(well_id: str, attribute: str) -> str:
    """Per-well tags: PWH (barg), TWH (degC), CHOKE (%), VFM_GAS (MMscfd), STATUS (1/0)."""
    return f"{well_id}.{attribute}"


# Network tags (all pressures in barg as historians record them).
NETWORK_TAGS: Final = {
    "manifold_pressure": "{node}.PRESS",  # barg, one per manifold node
    "suction_pressure": "HUBS.PRESS",
    "discharge_pressure": "HUBD.PRESS",
    "arrival_pressure": "OSC.PRESS",
    "hub_inlet_flow": "HUB-C.FLOW_IN",  # MMscfd, includes third-party gas
    "train_running": "HUB-C.T{n}.RUN",  # 1 running / 0 stopped
    "third_party_flow": "{stream}.FLOW",  # MMscfd
    "trunk_flow": "{pipe}.FLOW",  # MMscfd, metered at the slug catcher
    "plant_inlet_flow": "GPP.FLOW_IN",
    "plant_inlet_co2": "GPP.CO2_IN",  # mol%
    "sales_flow": "GPP.SALES_FLOW",  # fiscal, MMscfd
    "sales_ghv": "GPP.SALES_GHV",  # Btu/scf
    "ambient_temperature": "AMBIENT.TEMP",  # degC
}
WELL_ATTRIBUTES: Final = ("PWH", "TWH", "CHOKE", "VFM_GAS", "STATUS")
