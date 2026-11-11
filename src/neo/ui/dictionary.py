"""Data dictionary for the NeO internal explainer UI.

The single source of the words the UI shows for every landing-zone extract, historian tag,
register field, state field, advisory field, code and term. It is internal: it explains the
method, solver internals included. ``tests/test_dictionary.py`` derives the expected keys from
the source modules, so a new code or field fails the tests until it is documented here.

Numbers quoted for the reference network come from ``configs/reference`` and are synthetic:
assumptions to confirm with the client.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

__all__ = [
    "CODES",
    "EXTRACTS",
    "GLOSSARY",
    "OUTPUTS",
    "PIPELINE",
    "REGISTRY",
    "STATE",
    "TAGS",
    "Doc",
    "ExtractDoc",
    "StageDoc",
    "lookup",
]


@dataclass(frozen=True, slots=True)
class Doc:
    """``meaning`` is full sentences; ``unit``, ``source`` and ``used_for`` are fragments with no final period."""

    meaning: str  # 1-3 plain-English sentences a non-specialist can follow
    unit: str = ""
    source: str = ""  # real-world system that supplies it, or the NeO stage that produces it
    used_for: str = ""  # how NeO uses it, or what the reader should do with it


@dataclass(frozen=True, slots=True)
class ExtractDoc:
    file: str
    title: str
    mimics: str
    grain: str
    why: str
    fields: Mapping[str, Doc]


@dataclass(frozen=True, slots=True)
class StageDoc:
    key: str
    title: str
    does: str
    inputs: str
    outputs: str
    why: str
    module: str


_SYNTHETIC = "synthetic reference value, to confirm with the client"
_PI = "PI Data Archive (historian)"
_ASSET_REGISTRY = "Asset registry (asset_registry.yaml; in deployment PI AF, engineering files, limits register)"
_CONTRACTS = "GSA register (contracts.yaml; in deployment the commercial contract register)"
_PSC_TERMS = "PSC terms register (psc_terms.yaml; in deployment the PSC fiscal terms)"
_STATE_BUILDER = "Ingest + calibration (neo.ingest.state_builder), or a fixture"
_ADVISE = "Advisory pipeline (neo.advise)"
_NOT_USED = "Recorded for audit; not used by v1 logic"


def _assumed(source: str) -> str:
    return f"{source}; {_SYNTHETIC}"


# ----------------------------------------------------------------------------- extracts

EXTRACTS: dict[str, ExtractDoc] = {
    "historian": ExtractDoc(
        file="historian/<date>.parquet",
        title="Historian archive extract",
        mimics="PI Data Archive samples, tagged against PI Asset Framework, in UTC",
        grain="15-minute samples of every tag; one parquet file per UTC calendar date.",
        why=(
            "Today's operating point (well rates, pressures, trains running), the data gate, and 14 days of "
            "history for line-friction calibration."
        ),
        fields={
            "af_path": Doc(
                "Full PI Asset Framework path of the measured attribute (area, element, attribute), "
                r"e.g. \\NeO\P1\P1-W01|PWH.",
                source="PI Asset Framework",
                used_for="Traceability to the asset model; v1 logic keys on the tag, not the path",
            ),
            "tag": Doc(
                "PI point name, written <asset>.<ATTRIBUTE>, e.g. P1-W01.VFM_GAS or HUBS.PRESS (see the tag list).",
                source=_PI,
                used_for="Key for every lookup: the data gate, the snapshot and calibration select samples by tag",
            ),
            "ts_utc": Doc(
                "Time stamp of the sample, in UTC.",
                unit="UTC",
                source=_PI,
                used_for="Selects the 4 h snapshot window before the cut-off and the 14-day calibration history",
            ),
            "value": Doc(
                "The measured value in the tag's unit; empty when the sample is Bad.",
                unit="tag's uom",
                source=_PI,
                used_for=(
                    "Only Good samples count: their mean over the 4 h window is the snapshot value, and the "
                    "14-day series feeds line calibration"
                ),
            ),
            "uom": Doc(
                "Unit of measure: barg, degC, %, MMscfd, mol%, Btu/scf, or bool for on/off tags.",
                source="PI point attributes",
                used_for="Informational here; NeO reads units from tag_map.csv",
            ),
            "quality": Doc(
                "PI quality of the sample: Good, Questionable or Bad (see the quality codes).",
                source="PI Data Archive (instrument and interface status)",
                used_for="Only Good samples are used; the data gate counts Good samples of each critical tag",
            ),
            "substituted": Doc(
                "PI's flag that the value was replaced by hand or by the interface. Always False in synthetic data.",
                source=_PI,
                used_for=_NOT_USED,
            ),
            "digital_state": Doc(
                "PI system state text that accompanies a bad value, e.g. 'Bad Input'; empty for normal samples.",
                source=_PI,
                used_for="Recorded for audit; v1 relies on the quality column",
            ),
        },
    ),
    "tag_map": ExtractDoc(
        file="tag_map.csv",
        title="Tag map",
        mimics="PI AF element/attribute export: which PI tag measures which asset attribute",
        grain="One row per tag; static, changes only when instrumentation changes.",
        why=(
            "Says which tags are critical (must pass the data gate) and which are digital (exempt from the "
            "stuck-sensor rule)."
        ),
        fields={
            "tag": Doc("PI point name, as in the historian.", source="PI AF", used_for="Join key to the samples"),
            "asset_id": Doc(
                "Asset the tag belongs to: a well (P1-W01), node (HUBS), compressor or train (HUB-C, HUB-C.T1), "
                "plant (GPP), third-party stream (PAC-1), trunk line (T1) or AMBIENT.",
                source="PI AF",
                used_for="Grouping in the UI; not used by v1 logic",
            ),
            "attribute": Doc(
                "What is measured on the asset: PWH, TWH, CHOKE, VFM_GAS, STATUS, PRESS, FLOW, FLOW_IN, RUN, CO2_IN, "
                "SALES_FLOW, SALES_GHV or TEMP.",
                source="PI AF",
                used_for="Grouping in the UI; not used by v1 logic",
            ),
            "uom": Doc(
                "Unit of measure; 'bool' marks a digital (on/off) tag.",
                source="PI point attributes",
                used_for=(
                    "Digital tags are exempt from the stuck-sensor rule: train RUN and well STATUS legitimately "
                    "hold one value for days"
                ),
            ),
            "critical": Doc(
                "True if no advisory may be issued without this tag. Reference network: HUBS, HUBD and OSC "
                "pressures, hub inlet flow, train RUN tags, PAC-1 flow and plant inlet CO2.",
                source="Tag map. Criticality is an engineering decision; assumption to confirm with the client",
                used_for="Data gate: a critical tag that is bad, stale or stuck blocks the run",
            ),
        },
    ),
    "well_tests": ExtractDoc(
        file="well_tests.csv",
        title="Well tests",
        mimics="Well-test records from a multiphase flow meter (MPFM) or test separator",
        grain=(
            "One row per test. Synthetic: each well tested about every 30 days, staggered, 6/8/12 h long; "
            "about 5% rejected."
        ),
        why="The latest accepted test re-anchors each well's deliverability curve; its age sets confidence.",
        fields={
            "well_id": Doc("Well identifier, e.g. P1-W01 (platform P1, well 01).", used_for="Join to the registry"),
            "test_start_utc": Doc(
                "When the test started.",
                unit="UTC",
                source="Well-test database",
                used_for=(
                    "Only tests started before the cut-off count; the newest accepted one is used. Its age sets "
                    "sigma_rel and, beyond 60 days, the stale_test flag"
                ),
            ),
            "duration_h": Doc("Length of the test.", unit="h", source="Well-test database", used_for=_NOT_USED),
            "q_gas_mmscfd": Doc(
                "Gas rate measured during the test.",
                unit="MMscfd",
                source="MPFM or test separator",
                used_for="With pwh_barg, refits the curve coefficient C so the curve passes through this point",
            ),
            "q_cond_bbld": Doc(
                "Condensate rate measured during the test.",
                unit="bbl/d",
                source="MPFM or test separator",
                used_for="Not used by v1 (CGR comes from the registry); candidate for CGR updates",
            ),
            "q_water_bbld": Doc(
                "Water rate measured during the test.",
                unit="bbl/d",
                source="MPFM or test separator",
                used_for="Not used by v1 (WGR comes from the registry)",
            ),
            "pwh_barg": Doc(
                "Flowing wellhead pressure during the test.",
                unit="barg",
                source="Wellhead transmitter during the test",
                used_for="Converted to bara and used with q_gas_mmscfd to refit C",
            ),
            "choke_pct": Doc(
                "Choke opening during the test.",
                unit="%",
                source="Choke position during the test",
                used_for="Not used by v1; backlog: calibrate continuously from fully-open operating points",
            ),
            "co2_mol_pct": Doc(
                "CO2 content of the well's gas measured during the test.",
                unit="mol%",
                source="Test sample or online analyser",
                used_for="Not used by v1 (CO2 comes from the registry)",
            ),
            "accepted": Doc(
                "Whether the production engineer accepted the test as valid.",
                unit="True/False",
                source="Production engineer's test validation",
                used_for="Only accepted tests calibrate wells",
            ),
        },
    ),
    "lab_samples": ExtractDoc(
        file="lab_samples.csv",
        title="Lab gas analyses",
        mimics="Gas analyses from the PVT / analytical laboratory (LIMS)",
        grain="One sample per well and third-party stream; synthetic samples are 31-45 days before the run start.",
        why=(
            "Stream compositions drive the sales-gas quality limits. v1 reads and schema-checks this file but takes "
            "compositions from the asset registry; feeding lab results into the registry is a deployment step."
        ),
        fields={
            "sample_point": Doc(
                "Where the sample was taken: a well id (P1-W03) or a third-party stream id (PAC-1).",
                source="LIMS",
                used_for="Not used by v1",
            ),
            "sampled_utc": Doc("When the sample was taken.", unit="UTC", source="LIMS", used_for="Not used by v1"),
            "co2_mol_pct": Doc(
                "CO2 content of the gas.", unit="mol%", source="Lab analysis", used_for="Not used by v1"
            ),
            "h2s_ppmv": Doc(
                "Hydrogen sulphide content (toxic, corrosive).",
                unit="ppmv",
                source="Lab analysis",
                used_for="Not used by v1",
            ),
            "hg_ug_sm3": Doc(
                "Mercury content (damages aluminium heat exchangers in the plant).",
                unit="ug/Sm3",
                source="Lab analysis",
                used_for="Not used by v1",
            ),
            "ghv_btu_scf": Doc(
                "Gross heating value of the raw gas, CO2 included as a diluent.",
                unit="Btu/scf",
                source="Lab analysis (ISO 6976 calculation)",
                used_for="Not used by v1",
            ),
        },
    ),
    "events": ExtractDoc(
        file="events.csv",
        title="Operational events",
        mimics="Outage and event notifications (the brief names FFN and ASOME) and the maintenance plan",
        grain="One row per event. Unplanned events appear when they start; planned ones may appear ahead.",
        why="Removes capacity for the gas day: tripped or maintained trains, failed wells, plant and CO2 derates.",
        fields={
            "event_id": Doc("Unique event identifier, e.g. EV-TRIP-01.", source="Event system", used_for=_NOT_USED),
            "asset_id": Doc(
                "Affected asset: HUB-C (hub compressor station), GPP (gas plant) or a well id.",
                source="Event system",
                used_for=(
                    "A well failure makes that well unavailable; HUB-C events remove trains; GPP events reduce "
                    "plant capacities"
                ),
            ),
            "type": Doc(
                "Event type; see the event_type codes.",
                source="Event system",
                used_for=(
                    "Selects which capacity is reduced. Unplanned types (train_trip, well_failure) count only if "
                    "they started before the cut-off"
                ),
            ),
            "start_utc": Doc(
                "When the event starts.",
                unit="UTC",
                source="Event system",
                used_for="Events overlapping the cut-off-to-day-end window are applied",
            ),
            "end_utc": Doc(
                "When the event ends; empty means open-ended.",
                unit="UTC",
                source="Event system",
                used_for="Events overlapping the cut-off-to-day-end window are applied",
            ),
            "capacity_removed": Doc(
                "Capacity the event takes away, in the asset's own unit: trains for HUB-C, MMscfd for GPP, 1 for a "
                "well.",
                unit="trains / MMscfd / 1",
                source="Event system",
                used_for="Subtracted from installed trains, plant inlet capacity or CO2-removal capacity",
            ),
            "note": Doc(
                "Free-text description, e.g. 'Train trip on high vibration'.",
                source="Event system",
                used_for="Shown to people; not used by logic",
            ),
        },
    ),
    "nominations": ExtractDoc(
        file="nominations.csv",
        title="Nominations",
        mimics="The nomination system: buyers' day-ahead nominations and intraday renominations",
        grain="One row per gas day, contract and revision; synthetic day-ahead rows are submitted 10:00 local D-1.",
        why="What the plan must deliver first (stage 'deliver') and the reference for shortfall.",
        fields={
            "gas_day": Doc("Gas day the nomination is for.", unit="date", source="Nomination system"),
            "contract_id": Doc(
                "GSA the nomination is under, e.g. GSA-B1.",
                source="Nomination system",
                used_for="Join to contracts.yaml",
            ),
            "revision": Doc(
                "0 for the day-ahead nomination; 1, 2, ... for renominations.",
                source="Nomination system",
                used_for="The latest revision submitted before the cut-off is used",
            ),
            "qty_mmbtu_d": Doc(
                "Nominated quantity, in energy.",
                unit="MMBtu/d",
                source="Nomination system",
                used_for=(
                    "Delivery target for the contract, capped at MDQ. If missing, DCQ is assumed and "
                    "nomination_missing is flagged"
                ),
            ),
            "submitted_utc": Doc(
                "When the nomination was submitted.",
                unit="UTC",
                source="Nomination system",
                used_for="Nominations submitted after the cut-off are ignored",
            ),
        },
    ),
    "prices": ExtractDoc(
        file="prices.csv",
        title="Price assumptions",
        mimics="The commercial price deck",
        grain="One row per gas day.",
        why=(
            "Daily condensate and LNG prices. v1 does not read this file: it values condensate at "
            "contracts.yaml condensate_usd_bbl and excess gas at the excess contract's price."
        ),
        fields={
            "gas_day": Doc("Gas day the prices apply to.", unit="date", source="Commercial"),
            "condensate_usd_bbl": Doc(
                "Condensate price for the day.",
                unit="USD/bbl",
                source="Commercial price deck",
                used_for="Not read by v1; the register value is used",
            ),
            "lng_netback_usd_mmbtu": Doc(
                "Netback price of gas diverted to LNG, the priority-of-supply excess outlet.",
                unit="USD/MMBtu",
                source="Commercial price deck",
                used_for="Not read by v1; the POS-LNG contract price is used",
            ),
        },
    ),
    "third_party_schedule": ExtractDoc(
        file="third_party_schedule.csv",
        title="Third-party declared rates",
        mimics="Declared rates of third-party (PAC) gas sharing our hub, under a data-sharing arrangement",
        grain="One row per gas day and stream; may be missing (synthetic: about 10% of days).",
        why="Third-party gas uses our compression, lines and plant capacity and changes the blend.",
        fields={
            "gas_day": Doc("Gas day of the declaration.", unit="date", source="PAC data sharing"),
            "stream_id": Doc(
                "Third-party stream, e.g. PAC-1 (joins at the hub suction, HUBS).",
                source="PAC data sharing",
                used_for="Join to the registry's third_party list",
            ),
            "rate_mmscfd": Doc(
                "Declared rate for the day.",
                unit="MMscfd",
                source="PAC data sharing",
                used_for=(
                    "Fixed third-party inflow in the plan. If missing: the metered {stream}.FLOW, else the registry "
                    "nominal rate, and third_party_estimated is flagged"
                ),
            ),
            "source": Doc("Origin of the number ('declared').", source="PAC data sharing", used_for=_NOT_USED),
        },
    ),
    "terminal_plan": ExtractDoc(
        file="terminal_plan.csv",
        title="Terminal receiving plan",
        mimics="Condensate terminal management: how much condensate the terminal can receive",
        grain="One row per gas day.",
        why="Condensate production cannot exceed what the terminal can take (terminal_headroom limit).",
        fields={
            "gas_day": Doc("Gas day of the plan.", unit="date", source="Terminal management"),
            "receiving_headroom_bbld": Doc(
                "Condensate the terminal can receive on the day.",
                unit="bbl/d",
                source="Terminal management",
                used_for=(
                    "Right-hand side of the terminal_headroom limit. Day missing: latest earlier value; no plan at "
                    "all: the slug-catcher liquid limit (both flagged substituted)"
                ),
            ),
        },
    ),
    "dor_allocation": ExtractDoc(
        file="dor_allocation.csv",
        title="Allocated actuals (DOR)",
        mimics="The daily operations report / production allocation (the brief names DOR and Agnes)",
        grain="One row per gas day and well, published one day late.",
        why=(
            "The realised, allocated outcome of each day, to verify advice and build a benefit register. Never a "
            "model input. v1 reads but does not use it (backlog: neo verify against DOR)."
        ),
        fields={
            "gas_day": Doc("Gas day allocated.", unit="date", source="DOR / allocation system"),
            "well_id": Doc("Well the volumes are allocated to.", source="DOR / allocation system"),
            "gas_mmscfd": Doc(
                "Gas allocated to the well.",
                unit="MMscfd",
                source="DOR / allocation system",
                used_for="Verification only; not used by v1",
            ),
            "cond_bbld": Doc(
                "Condensate allocated to the well.",
                unit="bbl/d",
                source="DOR / allocation system",
                used_for="Verification only; not used by v1",
            ),
            "water_bbld": Doc(
                "Water allocated to the well.",
                unit="bbl/d",
                source="DOR / allocation system",
                used_for="Verification only; not used by v1",
            ),
            "method": Doc(
                "How totals were split to wells. 'pro_rata_vfm': fiscal gas shared by VFM rates, liquids by "
                "registry ratios.",
                source="DOR / allocation system",
                used_for="Tells the reader how much to trust a well's allocated number",
            ),
        },
    ),
    "registers": ExtractDoc(
        file="registers/",
        title="Engineering and commercial registers",
        mimics="PI AF and engineering files (asset registry), the GSA register and the PSC terms",
        grain="Effective-dated documents; they change only through MOC.",
        why="The static description of the network, its limits, contracts and fiscal terms.",
        fields={
            "asset_registry.yaml": Doc(
                "Topology, wells, pipes, compressor map, plant, MEG system and approved limits (model Network).",
                source=_ASSET_REGISTRY,
                used_for="Builds every physical row of the optimiser and every verifier check",
            ),
            "contracts.yaml": Doc(
                "Gas sales agreements and excess outlets with prices, priorities, DCQ/MDQ and shortfall penalties, "
                "plus the condensate price (model Commercial).",
                source=_CONTRACTS,
                used_for="Delivery priorities, the energy balance and the value objective",
            ),
            "psc_terms.yaml": Doc(
                "PSC blocks with their netback factor and priority-of-supply rank (model PscRegister).",
                source=_PSC_TERMS,
                used_for="Splits each well's value between the enterprise and partners/government",
            ),
        },
    ),
}


# ----------------------------------------------------------------------------- historian tags

TAGS: dict[str, Doc] = {
    "{node}.PRESS": Doc(
        "Pressure at a network node; one tag per node (manifolds P1M, P2M, P3M, and HUBS, HUBD, OSC).",
        unit="barg",
        source="PI: pressure transmitter at the node",
        used_for=(
            "P3M picks the route of the two-route platform P3; P1M and P2M, with the downstream node, calibrate "
            "their flowline's friction from 14 days of history"
        ),
    ),
    "HUBS.PRESS": Doc(
        "Hub compressor suction pressure. Critical.",
        unit="barg",
        source="PI: suction header transmitter on the hub",
        used_for=(
            "Today's suction pressure in the operating point; outlet pressure when calibrating FL-P1 and FL-P2 and "
            "when inferring P3's route. If bad, stale or stuck the run is blocked (the badtag scenario)"
        ),
    ),
    "HUBD.PRESS": Doc(
        "Hub compressor discharge pressure. Critical.",
        unit="barg",
        source="PI: discharge header transmitter on the hub",
        used_for="Inlet pressure for trunk calibration; tells whether P3 exports via the bypass line FL-P3D",
    ),
    "OSC.PRESS": Doc(
        "Arrival pressure at the onshore slug catcher. Critical.",
        unit="barg",
        source="PI: slug-catcher inlet transmitter",
        used_for="Outlet pressure for trunk calibration; the plan holds arrival at the registry set-point",
    ),
    "HUB-C.FLOW_IN": Doc(
        "Total gas entering the hub compressor, third-party gas included. Critical.",
        unit="MMscfd",
        source="PI: hub inlet flow meter",
        used_for=(
            "Reconciliation: hub-routed well rates plus third-party gas must add up to this meter; the wells' VFM "
            "rates are adjusted by weighted least squares"
        ),
    ),
    "HUB-C.T{n}.RUN": Doc(
        "Running status of compressor train n (n = 1..3): 1 running, 0 stopped. Critical, digital.",
        unit="bool (1/0)",
        source="PI: unit control panel status",
        used_for=(
            "Counts trains running at the cut-off (last value). Exempt from the stuck rule. More running than "
            "available raises train_status_mismatch"
        ),
    ),
    "{stream}.FLOW": Doc(
        "Metered flow of a third-party stream, e.g. PAC-1.FLOW. Critical.",
        unit="MMscfd",
        source="PI: allocation meter at the third-party tie-in",
        used_for="Fallback third-party rate when no declared schedule exists",
    ),
    "{pipe}.FLOW": Doc(
        "Flow in a trunk line (T1, T2), metered at the slug catcher.",
        unit="MMscfd",
        source="PI: trunk-line meter onshore",
        used_for="Trunk friction calibration with HUBD and OSC pressures",
    ),
    "GPP.FLOW_IN": Doc(
        "Gas plant inlet flow.",
        unit="MMscfd",
        source="PI: plant inlet meter",
        used_for="Recorded and shown in the UI; not read by v1 ingest",
    ),
    "GPP.CO2_IN": Doc(
        "CO2 content of the plant inlet gas from the online analyser. Critical.",
        unit="mol%",
        source="PI: plant inlet CO2 analyser",
        used_for="Gated as critical; its value is not yet used (backlog: intraday CO2 feedback)",
    ),
    "GPP.SALES_FLOW": Doc(
        "Fiscal sales-gas flow leaving the plant.",
        unit="MMscfd",
        source="PI: fiscal (custody-transfer) meter",
        used_for="Recorded; not read by v1 ingest. Allocation (DOR) is built from fiscal totals",
    ),
    "GPP.SALES_GHV": Doc(
        "Gross heating value of the sales gas.",
        unit="Btu/scf",
        source="PI: sales-gas analyser",
        used_for="Recorded; not read by v1 ingest",
    ),
    "AMBIENT.TEMP": Doc(
        "Ambient air temperature at the hub.",
        unit="degC",
        source="PI: weather station",
        used_for=(
            "Compressor driver power derates above design ambient (30 degC). If missing, design ambient is used "
            "and flagged"
        ),
    ),
    "<well>.PWH": Doc(
        "Flowing wellhead pressure, upstream of the choke.",
        unit="barg",
        source="PI: wellhead pressure transmitter",
        used_for="Recorded; v1 calibration uses the test's pwh_barg instead",
    ),
    "<well>.TWH": Doc(
        "Wellhead temperature.",
        unit="degC",
        source="PI: wellhead temperature transmitter",
        used_for="Recorded; v1 uses the registry wellhead temperature for choke and hydrate checks",
    ),
    "<well>.CHOKE": Doc(
        "Choke opening.",
        unit="%",
        source="PI: choke position transmitter",
        used_for="Recorded; not used by v1 (backlog: continuous calibration from fully-open points)",
    ),
    "<well>.VFM_GAS": Doc(
        "Gas rate from the well's virtual flow meter.",
        unit="MMscfd",
        source="PI: VFM application writing to PI",
        used_for=(
            "Today's well rate (4 h mean), then reconciled to the hub meter. If missing: the calibrated curve's rate "
            "at 40 bara, flagged substituted"
        ),
    ),
    "<well>.STATUS": Doc(
        "Well status: 1 flowing, 0 shut in. Digital.",
        unit="bool (1/0)",
        source="PI: well control / shutdown system status",
        used_for="Online if >= 0.5. If missing, a VFM rate above 0.5 MMscfd counts as online",
    ),
}


# ----------------------------------------------------------------------------- registers

REGISTRY: dict[str, dict[str, Doc]] = {
    "Node": {
        "id": Doc(
            "Node identifier: P1M, P2M, P3M (platform manifolds), HUBS / HUBD (hub suction / discharge), OSC "
            "(onshore slug-catcher arrival).",
            source=_ASSET_REGISTRY,
        ),
        "kind": Doc("Role of the node in the network; see the node_kind codes.", source=_ASSET_REGISTRY),
        "pressure_role": Doc(
            "Whether the node's pressure is set by control ('setpoint') or floats with flow ('free').",
            source=_ASSET_REGISTRY,
            used_for="HUBS suction is a decision of the optimiser; OSC arrival is fixed; others are computed",
        ),
        "maop_bara": Doc(
            "Maximum allowable operating pressure: the design and legal pressure ceiling of the node.",
            unit="bara",
            source=_ASSET_REGISTRY,
            used_for="Upper bound on the node pressure in the optimiser; verifier limit 'maop'",
        ),
        "p_min_bara": Doc(
            "Lowest allowed set-point of a set-point node (HUBS: 15).",
            unit="bara",
            source=_ASSET_REGISTRY,
            used_for="Lower bound of the suction decision; fallback suction when HUBS.PRESS is unusable",
        ),
        "p_max_bara": Doc(
            "Highest allowed set-point of a set-point node (HUBS: 45).",
            unit="bara",
            source=_ASSET_REGISTRY,
            used_for="Upper bound of the suction decision; range of the compressor planes",
        ),
        "setpoint_bara": Doc(
            "Fixed pressure of the arrival node (OSC: 61).",
            unit="bara",
            source=_assumed(_ASSET_REGISTRY),
            used_for="Arrival pressure held constant in the plan and the verifier",
        ),
    },
    "Pipe": {
        "id": Doc(
            "Line identifier: FL-P1, FL-P2 (flowlines to the hub), FL-P3S (P3 to hub suction), FL-P3D (P3 to hub "
            "discharge, bypassing compression), T1, T2 (subsea trunks to shore).",
            source=_ASSET_REGISTRY,
        ),
        "from_node": Doc("Upstream node of the line.", source=_ASSET_REGISTRY),
        "to_node": Doc("Downstream node of the line.", source=_ASSET_REGISTRY),
        "length_km": Doc(
            "Line length.", unit="km", source=_ASSET_REGISTRY, used_for="Weymouth friction coefficient K (prior)"
        ),
        "diameter_in": Doc(
            "Internal diameter.", unit="in", source=_ASSET_REGISTRY, used_for="Weymouth K prior; line volume"
        ),
        "efficiency": Doc(
            "Weymouth pipeline efficiency factor E (0-1): how much rougher than ideal the line behaves.",
            source=_ASSET_REGISTRY,
            used_for="Weymouth K prior; K is then refitted from data",
        ),
        "gas_sg": Doc(
            "Gas specific gravity relative to air (default 0.68).",
            source=_ASSET_REGISTRY,
            used_for="Weymouth K prior",
        ),
        "t_avg_c": Doc(
            "Average flowing gas temperature along the line.",
            unit="degC",
            source=_ASSET_REGISTRY,
            used_for="Weymouth K prior",
        ),
        "z_avg": Doc(
            "Average gas compressibility factor along the line (default 0.88).",
            source=_ASSET_REGISTRY,
            used_for="Weymouth K prior",
        ),
        "liquid_head_bar2": Doc(
            "Liquid-head term L: extra drop in squared pressure caused by liquid lying in the line at low flow "
            "(Flanigan).",
            unit="bar²",
            source=_assumed(_ASSET_REGISTRY),
            used_for="Pressure-drop curve K q² + L/(1 + beta q); kept at the prior during calibration",
        ),
        "holdup_beta": Doc(
            "How fast the liquid head falls as flow rises (beta in L/(1 + beta q)).",
            unit="1/MMscfd",
            source=_assumed(_ASSET_REGISTRY),
            used_for="Pressure-drop curve; kept at the prior during calibration",
        ),
        "elevation_s": Doc(
            "Elevation term s; the outlet pressure squared is multiplied by e^s (0 = level line).",
            source=_ASSET_REGISTRY,
            used_for="Pressure-drop equation",
        ),
        "capacity_mmscfd": Doc(
            "Maximum allowed flow in the line (design or erosional limit).",
            unit="MMscfd",
            source=_ASSET_REGISTRY,
            used_for="Optimiser row pipe_capacity (with back-off); verifier limit pipe_capacity",
        ),
        "parallel_group": Doc(
            "Name of a set of parallel lines (TRUNK for T1 and T2).",
            source=_ASSET_REGISTRY,
            used_for="Group members are calibrated from their own metered {pipe}.FLOW tag",
        ),
        "holdup_max": Doc(
            "Liquid holdup at zero flow, as a fraction of line volume (T1, T2: 0.08).",
            source=_assumed(_ASSET_REGISTRY),
            used_for="Liquid inventory model; above 0 enables the slug-sweep limit",
        ),
        "inventory_beta": Doc(
            "How fast the liquid inventory falls as flow rises (beta in V h_max/(1 + beta q)).",
            unit="1/MMscfd",
            source=_assumed(_ASSET_REGISTRY),
            used_for="Liquid released by a rate increase (slug_sweep)",
        ),
    },
    "HydrateParams": {
        "a": Doc(
            "Intercept of the hydrate curve T_h = a + b ln P.",
            unit="degC",
            source="Offline EOS fit per platform gas; registry",
            used_for="Hydrate temperature for the choke margin check and the MEG dose",
        ),
        "b": Doc(
            "Slope of the hydrate curve T_h = a + b ln P.",
            unit="degC per ln(bara)",
            source="Offline EOS fit per platform gas; registry",
            used_for="Hydrate temperature for the choke margin check and the MEG dose",
        ),
    },
    "Platform": {
        "id": Doc("Platform identifier: P1, P2, P3.", source=_ASSET_REGISTRY),
        "manifold": Doc("The platform's manifold node (P1M, P2M, P3M).", source=_ASSET_REGISTRY),
        "export_options": Doc(
            "Lines the platform can export through. P3 has two (FL-P3D, FL-P3S), so its route is a decision.",
            source=_ASSET_REGISTRY,
            used_for="Routing binaries in the MILP; route inference from pressures in ingest",
        ),
        "water_cap_bbld": Doc(
            "Produced-water handling capacity of the platform.",
            unit="bbl/d",
            source=_assumed(_ASSET_REGISTRY),
            used_for="Optimiser row and verifier limit platform_water",
        ),
        "opex_usd_per_mmscf": Doc(
            "Variable operating cost per unit of gas produced on the platform.",
            unit="USD/MMscf",
            source=_assumed("Finance / cost engineering"),
            used_for="The opex value component",
        ),
        "hydrate": Doc("Hydrate curve of the platform's gas (see HydrateParams).", source=_ASSET_REGISTRY),
    },
    "Well": {
        "id": Doc("Well identifier, e.g. P2-W04.", source=_ASSET_REGISTRY),
        "platform": Doc("Platform the well sits on.", source=_ASSET_REGISTRY),
        "psc": Doc(
            "PSC block the well belongs to (PSC-A, B, C).",
            source=_ASSET_REGISTRY,
            used_for="Netback factor in the value; POS rank in the greedy baseline",
        ),
        "c_wh": Doc(
            "Coefficient C of the wellhead deliverability curve q = C (p_ws² - p_wh²)^n (prior).",
            unit="MMscfd/(bara²)^n",
            source=f"{_ASSET_REGISTRY}; refitted daily from the latest accepted test",
            used_for="Deliverability rows and the verifier's well model",
        ),
        "n_wh": Doc(
            "Exponent n of the deliverability curve (0.5 = turbulent, 1 = laminar flow).",
            source=_ASSET_REGISTRY,
            used_for="Kept at the registry value in calibration",
        ),
        "p_ws_bara": Doc(
            "Static (shut-in) wellhead pressure at the survey date (the registry's valid_from).",
            unit="bara",
            source="Pressure survey; registry",
            used_for="Deliverability curve, after linear decline to the gas day",
        ),
        "p_ws_decline_bar_per_day": Doc(
            "Linear decline of the shut-in pressure since the survey.",
            unit="bar/d",
            source=_assumed("Reservoir engineering"),
            used_for="Shut-in pressure on the gas day",
        ),
        "rate_cap_mmscfd": Doc(
            "Maximum allowed rate of the well (erosion, sand or approved well limit).",
            unit="MMscfd",
            source=_ASSET_REGISTRY,
            used_for="Upper bound on the well rate; the harness opens 'open fully' wells to this cap",
        ),
        "min_rate_mmscfd": Doc(
            "Minimum stable rate: below it the well loads up with liquid (Turner).",
            unit="MMscfd",
            source=_assumed("Production engineering (Turner calculation offline)"),
            used_for="A well that is on must flow at least this much",
        ),
        "tubing_id_in": Doc(
            "Inner diameter of the production tubing.",
            unit="in",
            source="Well completion records",
            used_for="Input to the Turner minimum-rate formula; not used by the v1 optimiser",
        ),
        "wellhead_temp_c": Doc(
            "Flowing wellhead temperature.",
            unit="degC",
            source="Well records / historian average",
            used_for="Implied choke opening and the cooling across the choke (hydrate margin check)",
        ),
        "choke_bore_in": Doc(
            "Bore of the choke when fully open.",
            unit="in",
            source="Well equipment records",
            used_for="Converts a planned rate into an implied choke opening",
        ),
        "cgr_bbl_mmscf": Doc(
            "Condensate-gas ratio: barrels of condensate per million scf of gas.",
            unit="bbl/MMscf",
            source="Well tests / PVT. Constant per day in v1",
            used_for="Condensate value, slug-catcher liquids and terminal headroom",
        ),
        "wgr_bbl_mmscf": Doc(
            "Water-gas ratio: barrels of produced water per million scf of gas.",
            unit="bbl/MMscf",
            source="Well tests",
            used_for="Platform water, slug-catcher liquids and MEG demand",
        ),
        "co2": Doc(
            "CO2 mole fraction of the well's gas (0.28 = 28 mol%).",
            unit="mol fraction",
            source="Lab / test analyses; registry",
            used_for="CO2 removal load and sales-gas CO2 spec",
        ),
        "h2s_ppmv": Doc("H2S content.", unit="ppmv", source="Lab analyses; registry", used_for="h2s_inlet limit"),
        "hg_ug_sm3": Doc("Mercury content.", unit="ug/Sm3", source="Lab analyses; registry", used_for="hg_inlet limit"),
        "ghv_hc_btu_scf": Doc(
            "Gross heating value of the hydrocarbon part; the raw gas has (1 - co2) of it.",
            unit="Btu/scf",
            source="Lab analyses; registry",
            used_for="Sales energy, the GHV band and the partner share",
        ),
    },
    "TrainSpec": {
        "rated_power_kw": Doc(
            "Driver rated power of one compressor train (8,200).",
            unit="kW",
            source="Vendor data sheet; registry",
            used_for="Power-limited capacity of a train",
        ),
        "design_ambient_c": Doc(
            "Ambient temperature above which driver power derates (30).",
            unit="degC",
            source="Vendor data sheet",
            used_for="Ambient derate of capacity",
        ),
        "derate_per_c": Doc(
            "Fraction of driver power lost per degC above design ambient (0.007).",
            unit="1/degC",
            source="Vendor data sheet",
            used_for="Ambient derate of capacity",
        ),
        "polytropic_eff": Doc(
            "Polytropic efficiency of the compressor (0.78).", source="Vendor map", used_for="Head and power"
        ),
        "k": Doc("Ratio of gas heat capacities cp/cv (1.27).", source="Gas properties", used_for="Polytropic head"),
        "z": Doc("Gas compressibility factor at suction (0.9).", source="Gas properties", used_for="Head; flows"),
        "molar_mass": Doc(
            "Molar mass of the compressed gas (19.5).",
            unit="g/mol",
            source="Gas analysis",
            used_for="Mass flow and head",
        ),
        "suction_temp_c": Doc(
            "Gas temperature at compressor suction (32).",
            unit="degC",
            source="Process design",
            used_for="Head and actual volumetric flow",
        ),
        "stonewall_m3_s": Doc(
            "Maximum actual volumetric flow per train (stonewall / choke limit).",
            unit="m3/s",
            source="Vendor map",
            used_for="Upper cap on train capacity",
        ),
        "surge_m3_s": Doc(
            "Minimum actual volumetric flow per train before surge.",
            unit="m3/s",
            source="Vendor map",
            used_for="compressor_surge limit (plus the surge margin)",
        ),
        "ratio_max": Doc(
            "Maximum discharge/suction pressure ratio per train (3.6).",
            source="Vendor map",
            used_for="compressor_ratio limit",
        ),
        "surge_margin": Doc(
            "Control margin added to surge flow (0.10 = 10%).",
            source="Anti-surge control settings",
            used_for="compressor_surge limit",
        ),
    },
    "FuelSpec": {
        "thermal_eff": Doc(
            "Thermal efficiency of the gas-turbine driver (0.30).",
            source="Vendor data",
            used_for="Fuel gas burned per kW of shaft power",
        ),
        "lhv_btu_scf": Doc(
            "Lower heating value of the fuel gas (900).",
            unit="Btu/scf",
            source="Gas analysis",
            used_for="Converts fuel energy into fuel volume",
        ),
        "idle_power_kw": Doc(
            "Power a running train draws at zero flow (300).",
            unit="kW",
            source="Vendor data",
            used_for="Fixed fuel per running train",
        ),
    },
    "CompressorStation": {
        "id": Doc("Compressor station identifier (HUB-C).", source=_ASSET_REGISTRY),
        "suction_node": Doc("Suction node (HUBS).", source=_ASSET_REGISTRY),
        "discharge_node": Doc("Discharge node (HUBD).", source=_ASSET_REGISTRY),
        "trains": Doc(
            "Installed identical compressor trains (3).",
            source=_ASSET_REGISTRY,
            used_for="Trains available = installed minus trains removed by events",
        ),
        "train": Doc("Performance map of one train (see TrainSpec).", source=_ASSET_REGISTRY),
        "fuel": Doc("Fuel model of one train (see FuelSpec).", source=_ASSET_REGISTRY),
        "ps_min_bara": Doc(
            "Minimum allowed suction pressure (15).",
            unit="bara",
            source=_ASSET_REGISTRY,
            used_for="Verifier limit suction_pressure",
        ),
        "pd_max_bara": Doc(
            "Maximum allowed discharge pressure (92).",
            unit="bara",
            source=_ASSET_REGISTRY,
            used_for="Optimiser row pd_max; verifier limit discharge_pressure; MEG dose pressure",
        ),
        "design_ambient_c": Doc(
            "Station design ambient temperature (30).",
            unit="degC",
            source=_ASSET_REGISTRY,
            used_for="Substituted when AMBIENT.TEMP is unusable",
        ),
    },
    "ThirdPartyStream": {
        "id": Doc("Third-party stream identifier (PAC-1).", source=_ASSET_REGISTRY),
        "node": Doc("Node where it joins our network (HUBS).", source=_ASSET_REGISTRY),
        "nominal_rate_mmscfd": Doc(
            "Typical rate of the stream (40).",
            unit="MMscfd",
            source=_assumed(_ASSET_REGISTRY),
            used_for="Last-resort rate when neither a declaration nor a meter reading exists",
        ),
        "co2": Doc("CO2 mole fraction.", unit="mol fraction", source="Third-party data", used_for="Blending"),
        "h2s_ppmv": Doc("H2S content.", unit="ppmv", source="Third-party data", used_for="Blending"),
        "hg_ug_sm3": Doc("Mercury content.", unit="ug/Sm3", source="Third-party data", used_for="Blending"),
        "ghv_hc_btu_scf": Doc(
            "Hydrocarbon gross heating value.", unit="Btu/scf", source="Third-party data", used_for="Blending"
        ),
        "cgr_bbl_mmscf": Doc(
            "Condensate-gas ratio.", unit="bbl/MMscf", source="Third-party data", used_for="Liquids limits"
        ),
        "wgr_bbl_mmscf": Doc(
            "Water-gas ratio.", unit="bbl/MMscf", source="Third-party data", used_for="Liquids and MEG limits"
        ),
    },
    "SlugCatcher": {
        "node": Doc("Node of the slug catcher (OSC).", source=_ASSET_REGISTRY),
        "liquid_cap_bbld": Doc(
            "Liquid (condensate + water) the slug catcher can handle per day (24,000).",
            unit="bbl/d",
            source=_assumed(_ASSET_REGISTRY),
            used_for="slug_liquid limit; fallback terminal headroom",
        ),
        "free_volume_bbl": Doc(
            "Free volume available to receive liquid swept out of the trunks (6,000).",
            unit="bbl",
            source=_assumed(_ASSET_REGISTRY),
            used_for="slug_sweep limit",
        ),
        "sweep_fraction": Doc(
            "Share of the free volume a rate increase may use (0.8).",
            source=_ASSET_REGISTRY,
            used_for="slug_sweep limit",
        ),
    },
    "SalesSpec": {
        "co2_max": Doc(
            "Maximum CO2 in sales gas (0.08 = 8 mol%).",
            unit="mol fraction",
            source=_assumed("Sales-gas specification (PGU)"),
            used_for="sales_co2 limit; CO2 removal needed",
        ),
        "ghv_min_btu_scf": Doc(
            "Minimum sales-gas heating value (950).",
            unit="Btu/scf",
            source=_assumed("Sales-gas specification"),
            used_for="sales_ghv (min) limit",
        ),
        "ghv_max_btu_scf": Doc(
            "Maximum sales-gas heating value (1,100).",
            unit="Btu/scf",
            source=_assumed("Sales-gas specification"),
            used_for="sales_ghv (max) limit",
        ),
        "h2s_max_ppmv": Doc(
            "Maximum H2S (6).",
            unit="ppmv",
            source=_assumed("Sales-gas specification"),
            used_for="Applied to the plant inlet blend (h2s_inlet): conservative; confirm with the client",
        ),
        "hg_max_ug_sm3": Doc(
            "Maximum mercury (45).",
            unit="ug/Sm3",
            source=_assumed("Sales-gas specification"),
            used_for="Applied to the plant inlet blend (hg_inlet): conservative; confirm with the client",
        ),
    },
    "Plant": {
        "id": Doc("Gas processing plant identifier (GPP).", source=_ASSET_REGISTRY),
        "inlet_node": Doc("Node feeding the plant (OSC).", source=_ASSET_REGISTRY),
        "inlet_cap_mmscfd": Doc(
            "Plant inlet capacity before derates (520).",
            unit="MMscfd",
            source=_assumed(_ASSET_REGISTRY),
            used_for="plant_inlet limit, less active plant_derate events",
        ),
        "co2_removal_cap_mmscfd": Doc(
            "CO2 the plant can remove per day before derates (20).",
            unit="MMscfd of CO2",
            source=_assumed(_ASSET_REGISTRY),
            used_for="co2_removal limit, less active co2_removal_derate events",
        ),
        "shrink_frac": Doc(
            "Share of inlet volume lost to liquids extraction and plant fuel (0.04).",
            unit="fraction",
            source=_assumed("Plant performance"),
            used_for="Sales volume and energy",
        ),
        "spec": Doc("Sales-gas specification (see SalesSpec).", source=_ASSET_REGISTRY),
    },
    "MegSystem": {
        "cap_bbld": Doc(
            "Lean MEG injection capacity (900).",
            unit="bbl/d",
            source=_assumed(_ASSET_REGISTRY),
            used_for="meg limit",
        ),
        "lean_wt": Doc(
            "Strength of lean MEG as injected (0.9 = 90 wt%).",
            unit="mass fraction",
            source="MEG regeneration design",
            used_for="MEG dose per barrel of water",
        ),
        "seabed_temp_c": Doc(
            "Coldest temperature the gas meets in subsea lines (22).",
            unit="degC",
            source=_assumed("Metocean data"),
            used_for="Hydrate sub-cooling, hence MEG dose",
        ),
        "hydrate_margin_c": Doc(
            "Safety margin kept above hydrate temperature (3).",
            unit="degC",
            source="Flow-assurance practice",
            used_for="MEG dose and the choke_hydrate_margin warning",
        ),
    },
    "Contract": {
        "id": Doc(
            "Contract identifier: GSA-B1, GSA-B2 (sales agreements), POS-LNG (excess outlet).", source=_CONTRACTS
        ),
        "buyer": Doc("Buyer name, e.g. 'Buyer 1 (power)'.", source=_CONTRACTS, used_for="Shown in allocations"),
        "kind": Doc("Contract kind; see the contract_kind codes.", source=_CONTRACTS),
        "price_usd_mmbtu": Doc(
            "Price of gas sold under the contract (GSA-B1 7.5, GSA-B2 6.8, POS-LNG 8.2).",
            unit="USD/MMBtu",
            source=_assumed(_CONTRACTS),
            used_for="Gas sales value; DCQ-weighted GSA prices give the reference price",
        ),
        "priority": Doc(
            "Delivery priority, 1 = first. GSA shortfall weight is 10^(1 - priority).",
            source=f"{_CONTRACTS}; whether priority is contractual or price-driven is an open question",
            used_for="Deliver stage and valuation order. Not used for excess outlets (ranked by price)",
        ),
        "dcq_mmbtu_d": Doc(
            "Daily contract quantity: the buyer's normal daily entitlement.",
            unit="MMBtu/d",
            source=_CONTRACTS,
            used_for="Assumed nomination when none arrives; weights the reference price",
        ),
        "mdq_mmbtu_d": Doc(
            "Maximum daily quantity the seller must deliver if nominated (0 = no cap).",
            unit="MMBtu/d",
            source=_CONTRACTS,
            used_for="Caps the delivery target",
        ),
        "shortfall_usd_mmbtu": Doc(
            "Penalty per MMBtu delivered below nomination.",
            unit="USD/MMBtu",
            source=_assumed(_CONTRACTS),
            used_for="Rank stage only (merit order); not in the published value",
        ),
        "cap_mmbtu_d": Doc(
            "Maximum the excess outlet can take (POS-LNG: 40,000).",
            unit="MMBtu/d",
            source=_assumed(_CONTRACTS),
            used_for="Upper bound on excess sales",
        ),
    },
    "PscBlock": {
        "id": Doc("PSC block identifier (PSC-A, PSC-B, PSC-C).", source=_PSC_TERMS),
        "term_type": Doc(
            "PSC form, e.g. 'R/C' or 'Late Life Asset'.", source=_PSC_TERMS, used_for="Descriptive; not used by v1"
        ),
        "expiry": Doc("Date the PSC expires.", unit="date", source=_PSC_TERMS, used_for="Not used by v1"),
        "netback_factor": Doc(
            "Enterprise share (0-1) of the block's gas and condensate value; the rest goes to partners and "
            "government (A 0.55, B 0.65, C 0.50).",
            source=_assumed(_PSC_TERMS),
            used_for="partner_share and condensate value components",
        ),
        "pos_rank": Doc(
            "Priority-of-supply rank of the block, 1 = supplies first.",
            source=_PSC_TERMS,
            used_for="Greedy baseline's opening order only",
        ),
    },
    "Network": {
        "name": Doc("Name of the network model.", source=_ASSET_REGISTRY),
        "version": Doc(
            "Registry version (2026.11.12).",
            source=_ASSET_REGISTRY,
            used_for="Enters the run id and the manifest",
        ),
        "valid_from": Doc(
            "Date the registry takes effect; also the survey date for shut-in pressures.",
            unit="date",
            source=_ASSET_REGISTRY,
            used_for="Shut-in pressure decline; truth-plant days since survey",
        ),
        "gas_day": Doc("Gas-day calendar settings (see GasDayCalendarSpec).", source=_ASSET_REGISTRY),
        "nodes": Doc("All network nodes.", source=_ASSET_REGISTRY),
        "platforms": Doc("All platforms.", source=_ASSET_REGISTRY),
        "wells": Doc("All wells (17 in the reference network).", source=_ASSET_REGISTRY),
        "pipes": Doc("All flowlines and trunk lines.", source=_ASSET_REGISTRY),
        "compressor": Doc("The hub compressor station.", source=_ASSET_REGISTRY),
        "third_party": Doc("Third-party streams joining the network.", source=_ASSET_REGISTRY),
        "slug_catcher": Doc("The onshore slug catcher.", source=_ASSET_REGISTRY),
        "plant": Doc("The gas processing plant.", source=_ASSET_REGISTRY),
        "meg": Doc("The MEG injection system.", source=_ASSET_REGISTRY),
    },
    "GasDayCalendarSpec": {
        "timezone": Doc(
            "Time zone of the gas day (Asia/Kuala_Lumpur).",
            source=_ASSET_REGISTRY,
            used_for="Converts UTC samples to gas days",
        ),
        "start_hour": Doc(
            "Local hour the gas day starts (6).",
            unit="h",
            source=f"{_ASSET_REGISTRY}; the PGU gas-day definition is an open question",
            used_for="Gas-day bounds; advisory validity window",
        ),
        "nomination_cutoff_hour": Doc(
            "Local hour on the day before at which the planning snapshot is taken (11).",
            unit="h",
            source=f"{_ASSET_REGISTRY}; nomination windows are an open question",
            used_for="Snapshot cut-off: no data after it is used",
        ),
    },
    "Commercial": {
        "condensate_usd_bbl": Doc(
            "Condensate price (70).",
            unit="USD/bbl",
            source=_assumed(_CONTRACTS),
            used_for="The condensate value component",
        ),
        "contracts": Doc("GSAs and excess outlets (see Contract).", source=_CONTRACTS),
    },
    "PscRegister": {"blocks": Doc("All PSC blocks (see PscBlock).", source=_PSC_TERMS)},
    "Registry": {
        "network": Doc("The asset registry.", source=_ASSET_REGISTRY),
        "commercial": Doc("The GSA register and price assumptions.", source=_CONTRACTS),
        "psc": Doc("The PSC terms register.", source=_PSC_TERMS),
    },
}


# ----------------------------------------------------------------------------- network state

STATE: dict[str, dict[str, Doc]] = {
    "NetworkState": {
        "gas_day": Doc("Gas day being planned.", unit="date", source=_STATE_BUILDER),
        "revision": Doc(
            "Plan revision for the day: 0 day-ahead, higher numbers for intraday re-plans.",
            source=_STATE_BUILDER,
            used_for="Enters the run id; shown on the advisory",
        ),
        "snapshot_cutoff_utc": Doc(
            "Moment the data snapshot was taken (11:00 local on the day before).",
            unit="UTC",
            source=_STATE_BUILDER,
            used_for="Data after it is ignored",
        ),
        "wells": Doc("Per-well state (see WellState).", source=_STATE_BUILDER),
        "current": Doc("Settings in force at the cut-off (see OperatingPoint).", source=_STATE_BUILDER),
        "trains_available": Doc(
            "Compressor trains usable for the day: installed minus trains removed by trip or maintenance events.",
            source=_STATE_BUILDER,
            used_for="Upper bound of the train-count decision",
        ),
        "ambient_c": Doc(
            "Ambient temperature (4 h mean, or design ambient if missing).",
            unit="degC",
            source=_STATE_BUILDER,
            used_for="Compressor power derate",
        ),
        "third_party_rates": Doc(
            "Third-party inflow per stream for the day.",
            unit="MMscfd",
            source=_STATE_BUILDER,
            used_for="Fixed inflow in the plan",
        ),
        "pipe_hydraulics": Doc(
            "Calibrated pressure-drop parameters per line (K fitted; liquid head and elevation from the registry).",
            source=_STATE_BUILDER,
            used_for="pipe_dp rows and the verifier",
        ),
        "plant_inlet_cap_mmscfd": Doc(
            "Plant inlet capacity today, after derate events.",
            unit="MMscfd",
            source=_STATE_BUILDER,
            used_for="plant_inlet limit",
        ),
        "co2_removal_cap_mmscfd": Doc(
            "CO2 removal capacity today, after derate events.",
            unit="MMscfd",
            source=_STATE_BUILDER,
            used_for="co2_removal limit",
        ),
        "condensate_headroom_bbld": Doc(
            "Condensate the terminal can receive today.",
            unit="bbl/d",
            source=_STATE_BUILDER,
            used_for="terminal_headroom limit",
        ),
        "nominations_mmbtu_d": Doc(
            "Nomination per GSA used for the day.",
            unit="MMBtu/d",
            source=_STATE_BUILDER,
            used_for="Delivery targets and shortfall",
        ),
        "flags": Doc(
            "Data-quality flags raised while building the state (see DataFlag).",
            source=_STATE_BUILDER,
            used_for="Critical flags block the advisory; all appear under data_flags",
        ),
    },
    "WellState": {
        "well_id": Doc("Well identifier.", source=_STATE_BUILDER),
        "available": Doc(
            "False if a well_failure event is active: the well cannot be used today.",
            source=_STATE_BUILDER,
            used_for="Unavailable wells are fixed at zero",
        ),
        "online": Doc(
            "Whether the well was flowing at the cut-off (STATUS tag, else VFM rate).",
            source=_STATE_BUILDER,
            used_for="Current rate is zero when not online",
        ),
        "rate_mmscfd": Doc(
            "Current gas rate: VFM 4 h mean reconciled to the hub meter.",
            unit="MMscfd",
            source=_STATE_BUILDER,
            used_for="Starting point for step limits and the hold baseline",
        ),
        "curve": Doc(
            "Calibrated wellhead deliverability curve (C, n, p_ws).",
            source="Calibration (neo.calibrate.wells)",
            used_for="Deliverability rows and the verifier",
        ),
        "sigma_rel": Doc(
            "Relative 1-sigma uncertainty of deliverability: 0.03 + 0.001 per day of test age, max 0.20; 0.20 "
            "without a usable test.",
            unit="fraction",
            source="Calibration (neo.calibrate.wells)",
            used_for="Confidence label and the P10/P50/P90 value band",
        ),
        "test_age_days": Doc(
            "Age of the latest accepted test at the cut-off (infinite if none).",
            unit="days",
            source="Calibration (neo.calibrate.wells)",
            used_for="sigma_rel; stale_test flag beyond 60 days",
        ),
        "confidence": Doc(
            "Derived label: high if sigma_rel <= 0.05, medium if <= 0.10, else low.",
            source="WellState property",
            used_for="Shown per well; low raises low_deliverability_confidence",
        ),
    },
    "OperatingPoint": {
        "routes": Doc(
            "Export line in use per platform; for P3 inferred from which line's predicted manifold pressure best "
            "matches the measured one (no route tags yet).",
            source=_STATE_BUILDER,
            used_for="Hold baseline; reconciliation balance",
        ),
        "trains_running": Doc(
            "Trains running at the cut-off (RUN tags), capped at trains available.",
            source=_STATE_BUILDER,
            used_for="Hold baseline",
        ),
        "suction_bara": Doc(
            "Hub suction pressure at the cut-off (HUBS.PRESS in bara, else the minimum set-point).",
            unit="bara",
            source=_STATE_BUILDER,
            used_for="Hold baseline",
        ),
    },
    "DataFlag": {
        "code": Doc("What went wrong; see the data_flag codes.", source=_STATE_BUILDER),
        "subject": Doc(
            "What it concerns: a tag, well, stream, contract, line, 'AMBIENT' or 'terminal'.", source=_STATE_BUILDER
        ),
        "message": Doc("Human-readable detail.", source=_STATE_BUILDER),
        "severity": Doc(
            "info, warning or critical; critical blocks the advisory.",
            source=_STATE_BUILDER,
            used_for="Gate decision",
        ),
    },
}


# ----------------------------------------------------------------------------- advisory outputs

OUTPUTS: dict[str, dict[str, Doc]] = {
    "StatusReason": {
        "code": Doc("Machine code of the reason; see status_reason codes.", source=_ADVISE),
        "message": Doc("Human-readable explanation.", source=_ADVISE),
        "subject": Doc("What it concerns (contract, well, tag, limit row kind), or empty.", source=_ADVISE),
    },
    "WellReason": {
        "key": Doc(
            "Which term this is: an optimiser row kind, optionally ':<line or platform>', or a value component.",
            source="Merit order (neo.explain.merit)",
        ),
        "label": Doc("Plain-English name of the term, e.g. 'back-pressure in FL-P1'.", source="Merit order"),
        "usd_per_mmscfd": Doc(
            "This term's share of the well's unit value: positive adds value, negative is a cost to the network.",
            unit="USD/d per MMscfd",
            source="Ranking LP duals",
            used_for="Explains why the well ranks where it does (largest terms first, up to 6)",
        ),
    },
    "WellAdvice": {
        "item_id": Doc(
            "Advisory item id, 'well:<id>'.", source=_ADVISE, used_for="Links planner decisions to the item"
        ),
        "well_id": Doc("Well identifier.", source=_ADVISE),
        "platform": Doc("Platform of the well.", source=_ADVISE),
        "action": Doc("What to do with the well; see the action codes.", source=_ADVISE),
        "current_rate_mmscfd": Doc("Rate at the cut-off.", unit="MMscfd", source="Network state"),
        "recommended_rate_mmscfd": Doc(
            "Planned rate. For 'open fully' wells it is the expected rate, not a cap.",
            unit="MMscfd",
            source="Value LP stage",
            used_for="The set-point to approve",
        ),
        "implied_choke_pct": Doc(
            "Choke opening that gives the planned rate at the predicted pressures (capped at 100); empty if shut.",
            unit="%",
            source="Verifier (choke nozzle model)",
            used_for="Guide for the field; 100 means fully open",
        ),
        "wellhead_pressure_bara": Doc(
            "Predicted flowing wellhead pressure at the planned rate; empty if shut.",
            unit="bara",
            source="Verifier",
        ),
        "rank": Doc(
            "Position in the merit order, 1 = most valuable extra gas; empty if unavailable.",
            source="Merit order",
        ),
        "merit_status": Doc("Why the well sits where it does; see merit_status codes.", source="Merit order"),
        "unit_value_usd_per_mmscfd": Doc(
            "Value to the whole network of one more MMscfd from this well.",
            unit="USD/d per MMscfd",
            source="Ranking LP duals (off wells: forced-on re-solve)",
            used_for="Sorts the merit order and the choke-back order",
        ),
        "total_contribution_usd_d": Doc(
            "Change in day value from switching the well's status (on to off, or off to on), all else re-optimised; "
            "empty when not computed (harness).",
            unit="USD/d",
            source="Forced on/off re-solves of the ranking LP",
        ),
        "reasons": Doc("Largest terms of the unit value (see WellReason).", source="Merit order"),
        "confidence": Doc("Deliverability confidence; see confidence codes.", source="Calibration"),
        "flags": Doc("Extra notes on the well; see well_flag codes.", source=_ADVISE),
        "executing_role": Doc(
            "Role that would carry out the move (default 'PTOCC / FOM', roles named in the brief).",
            source=_ADVISE,
            used_for="Routing the instruction; confirm role names with the client",
        ),
    },
    "NetworkAdvice": {
        "suction_setpoint_bara": Doc(
            "Recommended hub compressor suction pressure.", unit="bara", source="Value LP stage"
        ),
        "trains_online": Doc("Compressor trains to run.", source="Value MILP stage"),
        "trains_available": Doc("Trains usable today.", source="Network state"),
        "routes": Doc("Export line per platform.", source="Value MILP stage"),
        "hub_inflow_mmscfd": Doc(
            "Gas entering the hub compressor, third-party included.", unit="MMscfd", source="Verifier"
        ),
        "compression_ullage_mmscfd": Doc(
            "Spare compression: running trains' capacity at the planned pressures minus hub inflow.",
            unit="MMscfd",
            source="Verifier + compressor map",
            used_for="How much more gas the hub could take",
        ),
        "discharge_pressure_bara": Doc("Predicted hub discharge pressure.", unit="bara", source="Verifier"),
        "arrival_pressure_bara": Doc("Arrival pressure at the slug catcher (registry set-point).", unit="bara"),
    },
    "SalesGas": {
        "volume_mmscfd": Doc("Predicted sales-gas volume.", unit="MMscfd", source="Verifier"),
        "ghv_btu_scf": Doc("Predicted sales-gas heating value.", unit="Btu/scf", source="Verifier"),
        "co2_mol_pct": Doc("Predicted sales-gas CO2.", unit="mol%", source="Verifier"),
        "co2_removal_mmscfd": Doc(
            "CO2 the plant must remove to meet the spec.",
            unit="MMscfd",
            source="Verifier",
            used_for="Compare with CO2 removal capacity",
        ),
    },
    "ContractAllocation": {
        "contract_id": Doc("GSA identifier.", source=_CONTRACTS),
        "buyer": Doc("Buyer name.", source=_CONTRACTS),
        "priority": Doc("Delivery priority, 1 = first.", source=_CONTRACTS),
        "nominated_mmbtu_d": Doc("Nomination used for the day.", unit="MMBtu/d", source="Network state"),
        "planned_mmbtu_d": Doc(
            "Energy the plan delivers to the contract (verified outcome allocated in priority order).",
            unit="MMBtu/d",
            source="Valuation (neo.verify.value)",
        ),
        "shortfall_mmbtu_d": Doc("Nomination minus planned delivery.", unit="MMBtu/d", source="Valuation"),
    },
    "ExcessAllocation": {
        "outlet_id": Doc("Excess outlet identifier (POS-LNG).", source=_CONTRACTS),
        "buyer": Doc("Buyer name.", source=_CONTRACTS),
        "planned_mmbtu_d": Doc("Energy sold to the outlet.", unit="MMBtu/d", source="Valuation"),
    },
    "ValueLine": {
        "component": Doc("Value component; see value_component codes.", source="Valuation"),
        "plan_usd_d": Doc("Component value under the plan.", unit="USD/d", source="Valuation of the verified plan"),
        "baseline_usd_d": Doc(
            "Component value if today's settings are held, as the plant would actually respond.",
            unit="USD/d",
            source="Valuation of the hold baseline (respond mode)",
        ),
        "delta_usd_d": Doc("Plan minus baseline.", unit="USD/d", source="Valuation", used_for="The value at stake"),
    },
    "BindingConstraint": {
        "key": Doc("Limit identifier: row kind, optionally ':<line or platform>'.", source="Ranking LP"),
        "label": Doc("Plain-English name of the limit.", source="Merit order"),
        "shadow_value": Doc(
            "Value of relaxing the limit by one unit (absolute dual, summed over the limit's rows; pressure limits "
            "converted to per bar).",
            source="Ranking LP duals",
            used_for="Which limits cost the most today; where debottlenecking pays",
        ),
        "unit": Doc("Unit of the shadow value, e.g. 'USD/d per MMscfd' or 'USD/d per bar'.", source="Merit order"),
    },
    "ValueRange": {
        "p10_usd_d": Doc(
            "10th percentile of plan value across 60 deliverability samples (low case).",
            unit="USD/d",
            source="Monte Carlo value band",
        ),
        "p50_usd_d": Doc("Median plan value across the samples.", unit="USD/d", source="Monte Carlo value band"),
        "p90_usd_d": Doc("90th percentile of plan value (high case).", unit="USD/d", source="Monte Carlo value band"),
    },
    "Advisory": {
        "schema_version": Doc("Version of the advisory contract (1.0).", source="neo.domain.advisory"),
        "run_id": Doc(
            "Deterministic id of the run: a hash of gas day, cut-off, revision, input files, registry version, code "
            "version and configuration.",
            source=_ADVISE,
            used_for="Same inputs give the same id; links decisions and manifest",
        ),
        "revision": Doc("Plan revision for the gas day.", source="Network state"),
        "supersedes": Doc("Run id this advisory replaces, if any.", source=_ADVISE),
        "gas_day": Doc("Gas day the advice is for.", unit="date"),
        "valid_from_utc": Doc("Start of the gas day.", unit="UTC", source="Gas-day calendar"),
        "valid_to_utc": Doc("End of the gas day.", unit="UTC", source="Gas-day calendar"),
        "snapshot_cutoff_utc": Doc("Data cut-off used.", unit="UTC", source="Network state"),
        "status": Doc("Overall verdict; see advisory_status codes.", source=_ADVISE),
        "status_reasons": Doc("Why the status is what it is (see StatusReason).", source=_ADVISE),
        "wells": Doc("Advice per well (see WellAdvice).", source=_ADVISE),
        "network": Doc("Network set-points (see NetworkAdvice); empty if blocked.", source=_ADVISE),
        "sales_gas": Doc("Predicted sales gas (see SalesGas); empty if blocked.", source=_ADVISE),
        "allocations": Doc("Delivery per GSA (see ContractAllocation).", source=_ADVISE),
        "excess": Doc("Sales to excess outlets (see ExcessAllocation).", source=_ADVISE),
        "value_waterfall": Doc("Plan vs hold, per value component (see ValueLine).", source=_ADVISE),
        "plan_value_usd_d": Doc("P10/P50/P90 plan value (see ValueRange).", source=_ADVISE),
        "marginal_gas_value_usd_mmbtu": Doc(
            "What one more MMBtu of our sales gas is worth today.",
            unit="USD/MMBtu",
            source="Dual of the energy balance in the ranking LP",
        ),
        "binding_constraints": Doc("Limits that bind, most valuable first (see BindingConstraint).", source=_ADVISE),
        "choke_back_order": Doc(
            "Flowing wells from lowest to highest unit value.",
            source="Merit order",
            used_for="If a limit tightens intraday, choke these first, in this order",
        ),
        "data_flags": Doc("Every data flag of the run, as reasons.", source="Network state"),
    },
    "DecisionRecord": {
        "run_id": Doc("Advisory run the decision is about.", source="Review UI"),
        "item_id": Doc("Advisory item decided, e.g. 'well:P1-W03'.", source="Review UI"),
        "decision": Doc("approve, edit or reject; see decision codes.", source="Review UI"),
        "reason_code": Doc(
            "Planner's reason code (list to agree with the client).",
            source="Review UI",
            used_for="Learning why advice is edited or rejected",
        ),
        "user": Doc("Who decided.", source="Review UI"),
        "decided_utc": Doc("When.", unit="UTC", source="Review UI"),
        "edited_value": Doc(
            "New value when the decision is 'edit', e.g. a rate.", unit="item's unit", source="Review UI"
        ),
    },
}


# ----------------------------------------------------------------------------- codes

_ACTIONS: dict[str, Doc] = {
    "increase": Doc(
        "Raise the well's rate to the recommended rate (change of 0.5 MMscfd or more).",
        used_for="Open the choke towards the implied opening",
    ),
    "decrease": Doc("Reduce the well's rate to the recommended rate.", used_for="Done first in the planner report"),
    "maintain": Doc("Change smaller than 0.5 MMscfd: leave the well as it is."),
    "open": Doc("Bring a shut-in well online at the recommended rate."),
    "open_fully": Doc(
        "Open the choke fully: the well is limited only by its own deliverability, so the network wants all it "
        "can give. The rate shown is expected, not a cap.",
        used_for="If a limit then tightens, choke back in the choke-back order",
    ),
    "shut_in": Doc("Close the well.", used_for="Done first in the planner report"),
    "unavailable": Doc("The well cannot be used today (failure event); no instruction."),
}

_ADVISORY_STATUS: dict[str, Doc] = {
    "ready": Doc("Plan verified against every hard limit; no reasons or data flags to read."),
    "ready_with_flags": Doc(
        "Plan verified and publishable, but there are status reasons (shortfall, relaxed step limits, warnings) "
        "or any data flag, info included.",
        used_for="Read the reasons and flags before approving",
    ),
    "blocked": Doc(
        "No advisory: critical data failed the gate, the limits cannot all hold, or the verifier still found a "
        "hard-limit breach after retries.",
        used_for="Operators keep current settings; status_reasons say why",
    ),
}

_CONFIDENCE: dict[str, Doc] = {
    "high": Doc("Deliverability uncertainty 5% or less: an accepted test 20 days old or newer."),
    "medium": Doc("Uncertainty 5-10%: latest accepted test about 21-70 days old."),
    "low": Doc(
        "Uncertainty above 10%, or no usable test.",
        used_for="Flagged low_deliverability_confidence; consider a well test before relying on the rate",
    ),
}

_MERIT_STATUS: dict[str, Doc] = {
    "at_max": Doc(
        "Unit value above +25 USD/d per MMscfd: the network wants more than the well can give; it is held by its "
        "own deliverability.",
        used_for="Becomes 'open fully'",
    ),
    "swing": Doc(
        "Unit value within 25 USD/d per MMscfd of zero: the well absorbs a binding network limit; its rate is set "
        "by that limit.",
        used_for="The well to trim or raise first when the limit moves",
    ),
    "at_min": Doc(
        "Unit value below -25: at the margin it costs the network more than it earns, so it sits at its minimum "
        "stable rate.",
        used_for="Choke or shut first",
    ),
    "off": Doc(
        "Available but shut in the plan. Its unit value is the value change from forcing it on, divided by its "
        "rate when on."
    ),
    "unavailable": Doc("Excluded today by a failure event; not ranked."),
}

_WELL_FLAGS: dict[str, Doc] = {
    "held_by_step_limit": Doc(
        "The destination plan wants this well more than 0.5 MMscfd higher, but today's move is already at the step "
        "limit.",
        used_for="Expect further increases on following days",
    ),
    "low_deliverability_confidence": Doc(
        "Deliverability confidence is low: the rate may be off by more than 10%.",
        used_for="Consider a well test",
    ),
    "choke_hydrate_margin": Doc(
        "Gas cooled by the pressure drop across the choke comes within the hydrate margin of hydrate temperature.",
        used_for="Check MEG or heating before moving the well",
    ),
    "open_fully": Doc(
        "The instruction is to open the choke fully (merit status at_max); the rate is an expectation.",
        used_for="Applies whether the well is flowing (action open_fully) or shut (action open)",
    ),
    "choke_fully_open": Doc(
        "The planned rate needs the choke 100% open; the well may fall short (full-open choke loss is not modelled).",
    ),
}

_DATA_FLAGS: dict[str, Doc] = {
    "critical_tag_bad": Doc(
        "A critical tag had no Good sample in the 4 h before the cut-off.",
        source="Data gate",
        used_for="Critical: blocks the advisory. Fix the instrument or the interface",
    ),
    "critical_tag_stale": Doc(
        "A critical tag's last Good sample is more than 2 h before the cut-off.",
        source="Data gate",
        used_for="Critical: blocks the advisory",
    ),
    "critical_tag_stuck": Doc(
        "A critical analog tag repeated the same value for its last 12 samples (3 h): likely a frozen sensor.",
        source="Data gate",
        used_for="Critical: blocks the advisory. Digital tags are exempt",
    ),
    "critical_tag_degraded": Doc(
        "Fewer than half of a critical tag's samples in the window were Good.",
        source="Data gate",
        used_for="Warning; the run continues on the Good samples",
    ),
    "no_accepted_test": Doc(
        "No usable accepted test before the cut-off (none, zero rate, or wellhead pressure above shut-in).",
        source="Well calibration",
        used_for="Registry curve used with 20% uncertainty (low confidence)",
    ),
    "stale_test": Doc(
        "The latest accepted test is more than 60 days old.",
        source="Well calibration",
        used_for="Schedule a test; confidence is falling",
    ),
    "substituted": Doc(
        "A missing input was replaced: a well rate (calibrated curve), ambient temperature (design value) or "
        "terminal headroom (latest plan or slug-catcher limit).",
        source="State builder",
        used_for="Info or warning; check the subject",
    ),
    "pipe_fit_clipped": Doc(
        "A line's fitted friction fell outside 0.5-2x the registry value and was clipped.",
        source="Line calibration",
        used_for="Suspect a meter or transmitter, or a real change such as liquid build-up",
    ),
    "train_status_mismatch": Doc(
        "More trains read as running than events leave available.",
        source="State builder",
        used_for="Info; check the event log and RUN tags",
    ),
    "third_party_estimated": Doc(
        "No declared third-party rate for the day; the metered flow (or nominal rate) was used.",
        source="State builder",
    ),
    "nomination_missing": Doc(
        "No nomination for the contract before the cut-off; DCQ was assumed.", source="State builder"
    ),
}

_LIMITS: dict[str, Doc] = {
    "well_deliverability": Doc(
        "Planned rate is within what the well can deliver at the computed manifold pressure.", unit="MMscfd"
    ),
    "choke_hydrate_margin": Doc(
        "Warning: gas temperature after the choke stays above hydrate temperature plus the margin.", unit="degC"
    ),
    "maop": Doc("Every node pressure stays at or below its MAOP.", unit="bara"),
    "pipe_capacity": Doc("Flow in every line stays at or below its capacity.", unit="MMscfd"),
    "compressor_capacity": Doc(
        "Hub inflow is within what the running trains can compress at the planned pressures and ambient (zero "
        "trains: zero inflow).",
        unit="MMscfd",
    ),
    "compressor_surge": Doc("Hub inflow stays above the trains' surge flow plus margin.", unit="MMscfd"),
    "compressor_ratio": Doc("Discharge/suction pressure ratio stays within the train maximum.", unit="ratio"),
    "discharge_pressure": Doc("Hub discharge pressure stays at or below its maximum.", unit="bara"),
    "suction_pressure": Doc("Hub suction pressure stays at or above its minimum.", unit="bara"),
    "plant_inlet": Doc("Plant inlet flow stays within today's inlet capacity.", unit="MMscfd"),
    "co2_removal": Doc("CO2 the plant must remove stays within today's removal capacity.", unit="MMscfd"),
    "sales_co2": Doc("Sales-gas CO2 meets the spec.", unit="mol fraction"),
    "sales_ghv_min": Doc("Sales-gas heating value is at or above the minimum.", unit="Btu/scf"),
    "sales_ghv_max": Doc("Sales-gas heating value is at or below the maximum.", unit="Btu/scf"),
    "h2s_inlet": Doc("H2S of the plant inlet blend is within the limit.", unit="ppmv"),
    "hg_inlet": Doc("Mercury of the plant inlet blend is within the limit.", unit="ug/Sm3"),
    "slug_liquid": Doc("Condensate plus water arriving onshore is within slug-catcher handling.", unit="bbl/d"),
    "terminal_headroom": Doc("Condensate is within what the terminal can receive.", unit="bbl/d"),
    "platform_water": Doc("Produced water on each platform is within its handling capacity.", unit="bbl/d"),
    "meg": Doc(
        "Lean MEG needed to protect the water at seabed temperature is within injection capacity.", unit="bbl/d"
    ),
    "slug_sweep": Doc(
        "Liquid pushed out of the trunks by a rate increase fits in the slug catcher's usable free volume.",
        unit="bbl",
    ),
    "move_limit": Doc(
        "Warning: each well's change stays within its step limit (max of 6 MMscfd and 25% of current rate).",
        unit="MMscfd",
        used_for="Not shown as a reason; step_limits_relaxed covers it",
    ),
}
_LIMITS = {
    code: replace(doc, source="Verifier (neo.verify.forward), check mode on the plan; respond mode on baselines")
    for code, doc in _LIMITS.items()
}

_WARNING_LIMITS_SHOWN = ("choke_hydrate_margin",)

_STATUS_REASONS: dict[str, Doc] = {
    "infeasible": Doc(
        "The hard limits cannot all hold today. One reason per conflicting limit row ('limit cannot hold: <row>').",
        used_for="Blocks the advisory. Check the named limits and the data behind them",
    ),
    "solve_failed": Doc(
        "A later solve stage failed; the message carries the solver status.", used_for="Blocks the advisory"
    ),
    "verification_failed": Doc(
        "After the retries the verifier still finds a hard-limit breach ('limit[subject]: value vs bound').",
        used_for="Blocks the advisory",
    ),
    "hold_breaches_limits": Doc(
        "Holding today's settings would breach the listed limits, as the plant would actually respond.",
        used_for="Acting on the advisory is needed to stay within limits",
    ),
    "step_limits_relaxed": Doc(
        "Current rates cannot be brought within limits using normal step sizes; larger moves are recommended."
    ),
    "shortfall": Doc("Planned delivery to the contract (subject) is below its nomination.", unit="MMBtu/d"),
    "unsold_gas": Doc(
        "Some of our sales energy has no outlet: GSAs are at nomination and excess outlets are full.",
        unit="MMBtu/d",
        used_for="Routine flaring is zero, so this is a plan problem to resolve",
    ),
}
_STATUS_REASONS |= {
    code: replace(_LIMITS[code], meaning="Verifier warning passed through: " + _LIMITS[code].meaning)
    for code in _WARNING_LIMITS_SHOWN
}
_STATUS_REASONS |= {
    code: replace(
        doc,
        meaning="Data flag passed through: " + doc.meaning,
        used_for="A reason (blocking) when critical; always listed under data_flags",
    )
    for code, doc in _DATA_FLAGS.items()
}

# Shared network limits: a row's unit is the unit of its shadow value in binding_constraints.
_NETWORK_ROWS: dict[str, Doc] = {
    "compressor_capacity": Doc(
        "Hub inflow under m x planes fitted below the train map in (suction², discharge²), with back-off; with "
        "zero trains (row compressor_off), no inflow."
    ),
    "compressor_surge": Doc("Hub inflow above m x surge flow, linearised in suction u."),
    "compressor_ratio": Doc("Discharge² at most ratio_max² x suction².", unit="USD/d per bar of discharge"),
    "pd_max": Doc("Discharge pressure² at most the maximum².", unit="USD/d per bar"),
    "pipe_dp": Doc(
        "Pressure drop along a line covers conservative chords of its convex drop curve (Weymouth + liquid head).",
        unit="USD/d per bar",
        used_for="Reason label 'back-pressure in <line>'",
    ),
    "pipe_capacity": Doc("Line flow within capacity (with back-off)."),
    "plant_inlet": Doc("Plant inlet flow within capacity (with back-off)."),
    "co2_removal": Doc("CO2 removed within capacity (with back-off)."),
    "sales_co2": Doc("CO2 left in sales gas within spec; fuel CO2 linearised at today's mix."),
    "sales_ghv": Doc(
        "Sales-gas energy between the minimum and maximum heating value.", unit="USD/d per Btu/scf-MMscfd"
    ),
    "h2s_inlet": Doc("H2S of the inlet blend within the limit (linear excess form)."),
    "hg_inlet": Doc("Mercury of the inlet blend within the limit (linear excess form)."),
    "slug_liquid": Doc("Condensate + water within slug-catcher handling (with back-off).", unit="USD/d per bbl/d"),
    "terminal_headroom": Doc("Condensate within terminal headroom.", unit="USD/d per bbl/d"),
    "platform_water": Doc("Water per platform within capacity.", unit="USD/d per bbl/d"),
    "meg": Doc("Water x MEG dose within injection capacity; built only when a dose is needed.", unit="USD/d per bbl/d"),
    "slug_sweep": Doc("Tangent of trunk liquid released within usable free volume.", unit="USD/d per bbl"),
    "energy_balance": Doc(
        "Our sales energy equals GSA deliveries plus excess sales.",
        unit="USD/MMBtu",
        used_for="Its dual is the marginal gas value and every well's 'gas sales' reason; never a binding limit",
    ),
}
_NETWORK_ROWS = {
    code: replace(
        doc,
        unit=doc.unit or "USD/d per MMscfd",
        used_for=doc.used_for or "Its dual prices each well's use of it (a reason) and, if non-zero, a binding limit",
    )
    for code, doc in _NETWORK_ROWS.items()
}

_STRUCTURAL_ROWS: dict[str, Doc] = {
    "well_status": Doc("Links a well's rate to its on/off binary: off means zero, on means between min rate and cap."),
    "well_deliverability": Doc(
        "Rate stays under conservative chords of the well's concave curve in manifold u = P² (relaxed when off)."
    ),
    "route_choice": Doc("Exactly one export line per two-route platform; flow only on the chosen line."),
    "train_choice": Doc("Exactly one compressor train count, from 0 to trains available."),
    "move": Doc("Step limit per well: change minus elastic slack within max(6 MMscfd, 25% of current rate)."),
    "commitment": Doc("Value stage keeps the deliver stage's weighted shortfall (plus 0.001)."),
    "restore_cap": Doc("Deliver and value stages keep the restore stage's total slack (plus 1e-6)."),
}
_STRUCTURAL_ROWS = {
    code: replace(doc, used_for="Well-specific or stage-linking row; never shown as a reason or binding limit")
    for code, doc in _STRUCTURAL_ROWS.items()
}

_ROW_KINDS = {
    code: replace(doc, source="Plan model (neo.opt.build)") for code, doc in (_NETWORK_ROWS | _STRUCTURAL_ROWS).items()
}

_VALUE_COMPONENTS: dict[str, Doc] = {
    "gas_sales": Doc("GSA deliveries and excess sales times their prices.", unit="USD/d"),
    "partner_share": Doc(
        "Negative: partners' and government share of our wells' gas, (1 - netback) x reference price x energy.",
        unit="USD/d",
    ),
    "condensate": Doc("Enterprise share of condensate: rate x CGR x price x netback.", unit="USD/d"),
    "opex": Doc("Negative: variable operating cost, rate x platform opex.", unit="USD/d"),
    "shortfall_penalty": Doc(
        "Negative: shortfall x contract penalty. Ranking stage only; not in the published waterfall.", unit="USD/d"
    ),
}
_VALUE_COMPONENTS = {
    code: replace(doc, source="Objective (neo.opt.build) and valuation (neo.verify.value)")
    for code, doc in _VALUE_COMPONENTS.items()
}

_STAGES: dict[str, Doc] = {
    "restore": Doc("MILP: minimise total elastic slack on step limits (how far beyond normal moves we must go)."),
    "deliver": Doc(
        "MILP: minimise priority-weighted shortfall (weight 10^(1 - priority): Buyer 1 x1, Buyer 2 x0.1), keeping "
        "the restore optimum."
    ),
    "value": Doc(
        "MILP: maximise enterprise value minus 120 USD/d per MMscfd moved, keeping both earlier optima; fixes "
        "on/off, routes and trains."
    ),
    "value_lp": Doc("LP: the value stage with integers fixed; its numbers are the published plan."),
    "rank": Doc(
        "LP with integers fixed, no step limits, value minus shortfall penalty: the destination plan.",
        used_for="Its duals give unit values, binding limits and the marginal gas value",
    ),
}
_STAGES = {code: replace(doc, source="Solver (neo.opt.solve)") for code, doc in _STAGES.items()}

_SCENARIOS: dict[str, Doc] = {
    "normal": Doc("No events; nominations around 97% of DCQ (sd 3%)."),
    "trip": Doc("One compressor train trips at 08:00 local on run day 3 (EV-TRIP-01) and returns 4 days later."),
    "co2cut": Doc("CO2 removal derated by 8 MMscfd (to 60% of 20) from run day 3 for 5 days (membrane fouling)."),
    "badtag": Doc(
        "HUBS.PRESS reads Bad for the 6 h before the cut-off for run day 4.",
        used_for="Tests that the data gate blocks",
    ),
}
_SCENARIOS = {code: replace(doc, source="neo.synth.scenarios") for code, doc in _SCENARIOS.items()}

_POLICIES: dict[str, Doc] = {
    "advisor": Doc(
        "NeO's set-points ('open fully' wells opened to their rate cap); the guard chokes in NeO's choke-back "
        "order. On a blocked day: hold."
    ),
    "hold": Doc("Keep yesterday's set-points; the guard chokes highest-CO2 wells first."),
    "greedy": Doc(
        "A planner's spreadsheet: open wells in PSC priority-of-supply order until nominations or the hub are full, "
        "then choke highest-CO2 wells until the CO2 load fits."
    ),
}
_POLICIES = {code: replace(doc, source="Harness (neo.harness)") for code, doc in _POLICIES.items()}

_SEVERITY: dict[str, Doc] = {
    "info": Doc("Data flag for awareness only.", source="State builder"),
    "warning": Doc(
        "Data flag that does not block, or verifier check that does not block.", source="State builder; verifier"
    ),
    "critical": Doc("Data flag that blocks the advisory.", source="Data gate"),
    "hard": Doc("Verifier breach of a hard limit: the plan is retried, then blocked.", source="Verifier"),
}

_EVENT_TYPES: dict[str, Doc] = {
    "train_trip": Doc("Unplanned compressor train stop; counts once started before the cut-off.", unit="trains"),
    "train_maintenance": Doc("Planned compressor train outage; may be known ahead.", unit="trains"),
    "well_failure": Doc("Unplanned well failure; the well becomes unavailable.", unit="1"),
    "co2_removal_derate": Doc("Reduced CO2 removal capacity at the plant.", unit="MMscfd"),
    "plant_derate": Doc("Reduced plant inlet capacity.", unit="MMscfd"),
    "pipe_restriction": Doc(
        "Reduced line capacity. Listed in the schema, but v1 ingest does not apply it yet.", unit="MMscfd"
    ),
}
_EVENT_TYPES = {code: replace(doc, source="events.csv type column") for code, doc in _EVENT_TYPES.items()}

CODES: dict[str, dict[str, Doc]] = {
    "action": _ACTIONS,
    "advisory_status": _ADVISORY_STATUS,
    "confidence": _CONFIDENCE,
    "merit_status": _MERIT_STATUS,
    "well_flag": _WELL_FLAGS,
    "status_reason": _STATUS_REASONS,
    "data_flag": _DATA_FLAGS,
    "limit": _LIMITS,
    "row_kind": _ROW_KINDS,
    "value_component": _VALUE_COMPONENTS,
    "stage": _STAGES,
    "scenario": _SCENARIOS,
    "policy": _POLICIES,
    "severity": _SEVERITY,
    "event_type": _EVENT_TYPES,
    "quality": {
        "Good": Doc("The sample is valid.", source=_PI, used_for="The only quality NeO uses"),
        "Questionable": Doc("The instrument or interface doubts the sample.", source=_PI, used_for="Ignored"),
        "Bad": Doc("The sample is invalid; the value is empty.", source=_PI, used_for="Ignored; counts against gate"),
    },
    "decision": {
        "approve": Doc("Planner accepts the item as advised.", source="Review UI"),
        "edit": Doc("Planner accepts with a changed value (edited_value).", source="Review UI"),
        "reject": Doc("Planner declines the item; reason_code says why.", source="Review UI"),
    },
    "node_kind": {
        "manifold": Doc("Platform header where wells combine.", source=_ASSET_REGISTRY),
        "hub_suction": Doc("Hub compressor suction (HUBS).", source=_ASSET_REGISTRY),
        "hub_discharge": Doc("Hub compressor discharge (HUBD).", source=_ASSET_REGISTRY),
        "arrival": Doc("Onshore arrival at the slug catcher (OSC).", source=_ASSET_REGISTRY),
    },
    "pressure_role": {
        "setpoint": Doc("Pressure held by control: a decision (HUBS) or fixed (OSC).", source=_ASSET_REGISTRY),
        "free": Doc("Pressure floats with flow; computed by the model.", source=_ASSET_REGISTRY),
    },
    "contract_kind": {
        "gsa": Doc("Gas sales agreement: nominated, prioritised, may carry shortfall penalties.", source=_CONTRACTS),
        "excess": Doc(
            "Outlet for gas beyond nominations (priority of supply, e.g. LNG), by price up to a cap.",
            source=_CONTRACTS,
        ),
    },
    "tag_kind": {
        "pressure": Doc("Pressure transmitter: white noise plus slow drift.", source="neo.synth.tags"),
        "temperature": Doc("Temperature element: white noise.", source="neo.synth.tags"),
        "flow": Doc("Process flow meter: per-meter bias plus noise.", source="neo.synth.tags"),
        "fiscal": Doc("Custody-transfer meter: small bias and noise.", source="neo.synth.tags"),
        "vfm": Doc("Virtual flow meter: larger bias (3%) plus noise.", source="neo.synth.tags"),
        "analyser": Doc("Online gas analyser: absolute noise in mol%.", source="neo.synth.tags"),
        "heating_value": Doc("Heating-value analyser: relative noise.", source="neo.synth.tags"),
        "choke": Doc("Choke position: noise while open, clipped to 0-100%.", source="neo.synth.tags"),
        "digital": Doc("On/off status: no noise; never 'stuck'.", source="neo.synth.tags"),
    },
}


# ----------------------------------------------------------------------------- pipeline

PIPELINE: list[StageDoc] = [
    StageDoc(
        key="ingest",
        title="Ingest the landing zone",
        does="Reads every extract and checks its columns; required files missing stop the run.",
        inputs="Landing-zone folder: registers, tag map, historian, well tests, events, nominations, schedules.",
        outputs="Validated tables, the registry, critical and digital tag lists, file hashes for the run id.",
        why="Validate at the boundary once; everything downstream trusts the data shape.",
        module="neo.ingest.landing, neo.ingest.schema",
    ),
    StageDoc(
        key="gate",
        title="Snapshot and data gate",
        does=(
            "Averages each tag's Good samples over the 4 h before the cut-off. A critical tag that is bad, stale "
            "(no Good sample for 2 h) or stuck (3 h flat) blocks the run."
        ),
        inputs="Historian samples, tag map.",
        outputs="Tag readings and data flags.",
        why="An advisory built on a frozen or missing critical measurement is worse than none.",
        module="neo.ingest.gate",
    ),
    StageDoc(
        key="calibrate",
        title="Calibrate and reconcile",
        does=(
            "Refits each well curve to its latest accepted test; reconciles VFM rates to the hub meter; infers "
            "routes; fits line friction from 14 days; applies events, schedules and nominations."
        ),
        inputs="Gate readings, well tests, events, nominations, third-party schedule, terminal plan, registry.",
        outputs="NetworkState: the advisor's validated view of the day.",
        why="The model must match today's plant, not the design book.",
        module="neo.ingest.state_builder, neo.calibrate.wells, neo.calibrate.pipes, neo.calibrate.reconcile",
    ),
    StageDoc(
        key="build",
        title="Build the planning model",
        does=(
            "Writes the network as a mixed-integer linear program in u = P²: well on/off, routing, train count, "
            "and one tagged row per limit."
        ),
        inputs="NetworkState, registry, model configuration.",
        outputs="A plan model per stage with tagged rows (see row_kind codes).",
        why="Limits inside the optimiser give shadow prices and guarantee the plan respects them.",
        module="neo.opt.build, neo.opt.pwl, neo.opt.lp",
    ),
    StageDoc(
        key="solve",
        title="Lexicographic solve",
        does=(
            "Runs restore, deliver, value (MILP), value_lp and rank (LP) with HiGHS. If restore is infeasible, an "
            "elastic copy names the limits that conflict."
        ),
        inputs="Plan models.",
        outputs="Plan numbers, stage results with duals, or an infeasibility diagnosis.",
        why="Step limits first, nominations second, value third, without inventing penalty prices.",
        module="neo.opt.solve",
    ),
    StageDoc(
        key="verify",
        title="Verify and value",
        does=(
            "Re-simulates the plan with the nonlinear physics and checks every hard limit in fixed code. On a "
            "breach: tighten back-offs and retry (2 times), else block. Also simulates holding today's settings."
        ),
        inputs="Plan numbers, calibrated physics, contracts.",
        outputs="Verified outcome, baseline outcome, value parts, allocations.",
        why="Catches linearisation errors before anyone acts on the plan.",
        module="neo.advise, neo.verify.forward, neo.verify.value",
    ),
    StageDoc(
        key="explain",
        title="Merit order and value band",
        does=(
            "Turns ranking-LP duals into each well's unit value and reasons; finds binding limits and the marginal "
            "gas value; builds the choke-back order; samples deliverability 60 times for P10/P50/P90."
        ),
        inputs="Rank stage, plan, NetworkState.",
        outputs="Merit order, binding limits, value band.",
        why="Planners act on advice they can understand and challenge.",
        module="neo.explain.merit, neo.advise",
    ),
    StageDoc(
        key="publish",
        title="Publish",
        does=(
            "Writes advisory.json, advisory_report.md, nomination_draft.csv, decision_log.csv (header only) and "
            "the internal run_manifest.json to data/out/<gas-day>/rev<n>-<run_id>/."
        ),
        inputs="Advisory, manifest, nomination draft.",
        outputs="Run folder.",
        why="Versioned, reproducible artefacts; nothing writes to control systems.",
        module="neo.publish.writers, neo.explain.text, neo.domain.advisory",
    ),
    StageDoc(
        key="synth",
        title="Synthetic truth plant",
        does=(
            "Simulates a 'real' plant whose parameters differ from the registry, adds sensor noise, faults and "
            "events, and writes a landing zone in the exact extract layout."
        ),
        inputs="Registry, seed, scenario.",
        outputs="Landing zone; true daily outcomes.",
        why="Tests NeO on data its own model did not generate (no inverse crime).",
        module="neo.synth, neo.synth.truth, neo.synth.extracts, neo.synth.historian, neo.synth.noise",
    ),
    StageDoc(
        key="harness",
        title="Closed-loop harness",
        does=(
            "Each gas day: truth to extracts to NeO to a simulated control room to truth. Hold and greedy are "
            "replayed on the same true day under the same protection guard."
        ),
        inputs="Registry, scenarios, days, seed.",
        outputs="days.csv per scenario and summary.md with paired uplift and 90% CIs.",
        why="Measures value and safety against incumbent practice before any client trial.",
        module="neo.harness.loop, neo.harness.operator, neo.harness.baselines, neo.harness.metrics",
    ),
]


# ----------------------------------------------------------------------------- glossary

GLOSSARY: dict[str, str] = {
    # Commercial and fiscal
    "GSA": "Gas sales agreement: a long-term contract to deliver gas to a buyer, with DCQ, MDQ, price and penalties.",
    "DCQ": "Daily contract quantity: the buyer's normal daily entitlement under a GSA (MMBtu/d).",
    "MDQ": "Maximum daily quantity: the most the seller must deliver on a day if nominated (MMBtu/d).",
    "ACQ": "Annual contract quantity under a GSA. Not modelled in v1, which plans one day at a time.",
    "take-or-pay": (
        "GSA clause where the buyer pays for a minimum annual quantity even if not taken. Multi-day, so not "
        "modelled in v1."
    ),
    "POS / priority of supply": (
        "The order in which sources or outlets are served. NeO uses contract priority for GSAs; the PSC POS rank "
        "drives only the greedy baseline."
    ),
    "excess outlet": "A buyer for gas beyond nominations (here POS-LNG, LNG diversion), sold by price up to a cap.",
    "PSC": "Production sharing contract: the fiscal regime that splits a block's production between partners.",
    "R/C PSC": "Revenue-over-cost PSC: profit share depends on cumulative revenue relative to cost.",
    "Late Life Asset PSC": "PSC terms for mature fields; descriptive in v1 (netback factor is what NeO uses).",
    "netback factor": "Enterprise share (0-1) of a PSC block's gas and condensate value.",
    "enterprise value": (
        "What NeO maximises: gas sales minus partners' and government share, plus our share of condensate, minus "
        "variable opex (USD/d)."
    ),
    "reference price": "DCQ-weighted average GSA price (about 7.29 USD/MMBtu): values the partner share of gas.",
    "PAC": (
        "The brief's name for third-party gas that shares our hub (PAC-1 joins at HUBS). Expansion and data "
        "cadence to confirm with the client."
    ),
    "third-party gas": "Gas from another operator using our compression, lines and plant; it takes capacity.",
    "nomination": "A buyer's request for the gas day (MMBtu/d), submitted day-ahead; renominations revise it.",
    "renomination": "An intraday revision of a nomination (revision 1, 2, ...).",
    "shortfall": "Nomination minus delivery for a GSA (MMBtu/d).",
    "PGU": "Peninsular Gas Utilisation: the transmission system sales gas enters; its rules are open questions.",
    "LNG diversion": "Selling excess gas to LNG under priority of supply (POS-LNG in the reference contracts).",
    # Units
    "MMscfd": "Million standard cubic feet per day: gas volume rate at 60 degF, 14.696 psia. NeO's rate unit.",
    "MMscf": "Million standard cubic feet of gas.",
    "MMBtu": "Million British thermal units. Gas is sold by energy: MMscfd x Btu/scf = MMBtu/d.",
    "bbl/d": "Barrels per day of liquid (1 bbl = 159 litres).",
    "bara": "Absolute pressure in bar (gauge + 1.01325). NeO's physics and outputs use bara.",
    "barg": "Gauge pressure, relative to atmosphere. Historians and tests record barg; ingest adds 1.01325.",
    "mol%": "Mole percent: share of molecules (equal to share of standard volume for gases).",
    "ppmv": "Parts per million by volume.",
    "ug/Sm3": "Micrograms per standard cubic metre (mercury content).",
    # Gas quality
    "GHV": "Gross heating value (Btu/scf): energy from burning gas, water condensed. Blends linearly by volume.",
    "Wobbe index": "Heating value over the square root of relative density. Not computed in v1 (not linear).",
    "CGR": "Condensate-gas ratio (bbl/MMscf). Constant per day in the advisor; falls with pressure in the truth plant.",
    "WGR": "Water-gas ratio (bbl/MMscf). Rises slowly over time in the truth plant.",
    "CO2 removal": "Plant unit (membrane or amine) that takes CO2 out so sales gas meets spec; capacity in MMscfd.",
    "shrinkage": "Inlet volume lost in the plant to liquids extraction and fuel (4% in the reference plant).",
    "sales gas spec": "Limits on CO2, heating value, H2S and mercury of gas sold into the grid.",
    "PVT": "Pressure-volume-temperature lab analysis of reservoir fluids.",
    # Wells and network
    "wellhead": "Top of the well, upstream of the choke, where PWH and TWH are measured.",
    "choke": (
        "Adjustable restriction that sets a well's rate by taking the wellhead-to-manifold pressure drop. The "
        "optimiser treats it as a free pressure absorber."
    ),
    "implied choke opening": "Opening that delivers the planned rate at predicted pressures (nozzle model), in %.",
    "manifold": "Platform header where wells combine; its pressure is the back-pressure every well flows against.",
    "flowline": "Line from a platform to the hub (FL-P1, FL-P2, FL-P3S) or past compression (FL-P3D).",
    "trunk line": "Long subsea line from the hub to shore (T1 24 in, T2 20 in, 150 km, in parallel).",
    "hub": "Platform where flows combine and are compressed (HUBS suction, HUBD discharge, compressor HUB-C).",
    "bypass": "Route FL-P3D sends P3 gas straight to hub discharge, skipping compression (P3 has high pressure).",
    "slug catcher": "Onshore vessel at the trunk outlet that separates arriving liquids and absorbs slugs.",
    "slug": "A large body of liquid pushed through a line, e.g. when flow rises and sweeps out liquid inventory.",
    "liquid holdup": "Liquid lying in a gas line; it grows at low flow and is released when flow rises.",
    "linepack": "Gas stored in a line by its pressure. Not modelled in v1 (steady state).",
    "MEG": "Mono-ethylene glycol injected with wet gas to prevent hydrates; regenerated onshore.",
    "hydrate": "Ice-like solid of water and gas forming at high pressure and low temperature; can block lines.",
    "Hammerschmidt": "Classic equation for the inhibitor concentration that lowers hydrate temperature by dT.",
    "Joule-Thomson cooling": "Gas cools as it drops pressure across a choke; checked against hydrate temperature.",
    "deliverability": "Rate a well can produce against a given wellhead pressure.",
    "IPR": "Inflow performance relationship: reservoir rate vs bottomhole pressure (truth plant side).",
    "shut-in pressure (p_ws)": "Wellhead pressure of a closed well; the top of the deliverability curve.",
    "Turner rate": "Minimum gas rate that lifts liquid droplets out of a well (Turner 1969); below it, it loads up.",
    "liquid loading": "Liquid accumulating in a well that flows too slowly, choking it off.",
    "condensate banking": (
        "Condensate dropping out near the wellbore as pressure falls, cutting gas deliverability and CGR."
    ),
    "back-pressure": "Pressure a well or line flows against; more flow in a shared line raises it for everyone.",
    "Weymouth equation": "Classic gas-line equation: drop in squared pressure proportional to flow squared.",
    "Flanigan correction": "Adds a liquid-head term to the pressure drop that grows at low gas velocity.",
    "MAOP": "Maximum allowable operating pressure of equipment or a line.",
    "ullage": "Spare capacity; compression ullage = running trains' capacity minus hub inflow.",
    # Compression
    "compressor train": "One driver-and-compressor set; the hub has 3 identical trains sharing load equally.",
    "surge": "Flow reversal in a centrifugal compressor at too low a flow; kept above surge flow + 10%.",
    "stonewall": "Maximum flow a compressor can pass (sonic limit); with driver power it caps train capacity.",
    "compression ratio": "Discharge over suction absolute pressure; at most 3.6 per train here.",
    "polytropic head": "Energy per kg the compressor adds; with flow it sets the power needed.",
    "ambient derate": "Gas-turbine power falls on hot days (0.7% per degC above 30 degC here).",
    "fuel gas": "Gas burned by the compressor drivers, taken from the hub stream with its CO2.",
    # Operations and data
    "gas day": "The 24 h contractual day; assumed 06:00-06:00 Malaysia time. PGU's definition to confirm.",
    "cut-off": "Snapshot time for planning a gas day: 11:00 local the day before. Later data is ignored.",
    "DOR": "Daily operations report / allocated production per well; used only to verify, never as model input.",
    "allocation": "Splitting fiscal totals back to wells (here pro rata to VFM rates).",
    "VFM": "Virtual flow meter: a model estimating a well's rate from pressures, temperatures and choke.",
    "MPFM": "Multiphase flow meter: measures gas, condensate and water without separating them.",
    "well test": "Measuring a well's rates and pressures, via MPFM or a test separator; accepted by an engineer.",
    "PI / historian": "OSIsoft/AVEVA PI System: time-series archive of plant measurements (tags).",
    "AF": "PI Asset Framework: the asset hierarchy that tags hang off.",
    "OPC UA quality": "Industrial data-quality states (Good, Uncertain, Bad); PI's Good/Questionable/Bad mirror it.",
    "MOC": "Management of change: approval process for changing limits, registers or set-points.",
    "FFN / ASOME": "Client event and operations systems named in the brief; content to confirm with the client.",
    "Agnes": "Allocation system named in the brief alongside DOR; to confirm with the client.",
    "PTOCC / FOM": "Execution roles named in the brief (default executing_role); confirm with the client.",
    "GPP": "The reference network's onshore gas processing plant.",
    # Optimisation method
    "MILP": "Mixed-integer linear program: linear model with some yes/no decisions (on/off, route, train count).",
    "LP": "Linear program: no integers; gives duals (shadow prices), which NeO uses to explain.",
    "HiGHS": "Open-source LP/MILP solver used through highspy.",
    "binary variable": "A 0/1 decision: y (well on), r (route), z (train count).",
    "u = P²": "NeO works in squared pressure; gas line drops and well curves become linear-friendly in it.",
    "piecewise-linear chord": "Straight segments between points on a curve (tolerance 0.05 MMscfd wells, 6 bar² lines).",
    "conservative linearisation": (
        "Chords below concave well curves, above convex line drops, planes below the compressor map: a linear-"
        "feasible plan is physically feasible."
    ),
    "compressor planes": "Train capacity approximated by planes in (suction², discharge²), shifted below the map.",
    "lexicographic objective": (
        "Optimise priorities in order (step limits, nominations, value); each later stage keeps the earlier optimum."
    ),
    "shadow price / dual": "Change in objective per unit relaxation of a limit; zero if the limit is not binding.",
    "reduced cost": "A variable's objective coefficient minus the dual-weighted use of all rows it appears in.",
    "binding limit": "A limit the plan presses against; relaxing it would add value (non-zero shadow price).",
    "marginal gas value": "Value of one more MMBtu of our sales gas today (USD/MMBtu): the energy-balance dual.",
    "merit order": "Wells sorted by unit value, most valuable extra MMscfd first.",
    "unit value": (
        "Value to the network of one more MMscfd from a well: direct value minus what it uses of shared limits, "
        "priced by their duals."
    ),
    "total contribution": "Day value change from switching a well on or off, everything else re-optimised.",
    "swing well": "A well whose rate a binding network limit sets; unit value about zero.",
    "open fully": "Instruction for wells held only by their own deliverability; the rate shown is an expectation.",
    "choke-back order": "Flowing wells from lowest to highest unit value: who gives way first if a limit tightens.",
    "destination plan": "Rank-stage plan with no step limits: where the network should head over coming days.",
    "back-off": (
        "Safety margin: capacities cut 2% in the optimiser, +2 points per verify retry. Deliverability back-off "
        "starts at 0."
    ),
    "step limit / move limit": "Largest change per well per day: max(6 MMscfd, 25% of current rate).",
    "elastic slack": "Allowed violation of a soft row (step limits), minimised first; non-zero means larger moves.",
    "swing penalty": "120 USD/d per MMscfd moved, in the value stage: avoids needless moves.",
    "priority weight": "10^(1 - priority) on GSA shortfall in the deliver stage (1, 0.1, ...).",
    "elastic diagnosis": "Re-solve with slack on every hard row; rows needing slack are the conflicting limits.",
    "verifier": "Nonlinear forward simulation that checks every hard limit in fixed code, independent of the MILP.",
    "check mode": "Verifier mode: rates as planned, pressures recomputed exactly, limits checked.",
    "respond mode": (
        "Plant-response mode: wells give at most their deliverability, suction rises if compression falls short."
    ),
    "limit margin": "Distance to a limit in its own unit; negative means breached.",
    "utilisation": "Share of a limit used (upper limits) or of a floor cleared (lower limits).",
    "value waterfall": "Plan vs hold value per component (gas sales, partner share, condensate, opex).",
    "hold baseline": "Today's settings held, simulated in respond mode: what doing nothing would yield.",
    "P10 / P50 / P90": (
        "Percentiles of plan value over 60 deliverability samples. Here P10 is the low case, unlike reserves "
        "convention."
    ),
    "Monte Carlo": "Repeated random sampling; here of well deliverability, to get a value band.",
    # Data and calibration
    "landing zone": "Folder of source-system extracts in a fixed layout (neo.ingest.schema).",
    "extract": "One file a source system delivers into the landing zone.",
    "data gate": "Checks critical tags before planning; bad, stale or stuck blocks the run.",
    "critical tag": "A measurement the advisory cannot do without (tag_map critical = True).",
    "stuck sensor": "A reading that stops changing but still reports Good quality.",
    "substitution": "Replacing a missing input with a fallback value, always flagged.",
    "reconciliation": "Adjusting well rates by weighted least squares so they add up to the hub meter.",
    "calibration": "Fitting model parameters to data: well C from tests, line friction K from 14 days of history.",
    "route inference": "Choosing a platform's active line by which one's predicted pressure matches the gauge.",
    "RTO": "Real-time optimisation: re-calibrate the model, then optimise, on a fixed cycle (here daily).",
    "fixture": "Registry-derived state for tests and demos, skipping ingest and calibration.",
    "run id": "Deterministic hash of the inputs, registry, code version and configuration.",
    "run manifest": "Internal JSON with configuration, timings, solve attempts and verifier detail.",
    "decision log": "Planner approve/edit/reject per advisory item (filled by the review UI).",
    # Evaluation
    "truth plant": "Synthetic 'real' plant with parameters drawn away from the registry and effects NeO omits.",
    "inverse crime": "Testing a model on data it generated itself; avoided by the separate truth plant.",
    "keyed random stream": "Random draws keyed by (seed, what, which), so any day replays identically.",
    "scenario": "Named test condition for the truth plant: normal, trip, co2cut, badtag.",
    "harness": "Closed loop: truth, extracts, NeO, control room, truth; compares NeO with hold and greedy.",
    "guard": (
        "The simulated control room's rule for every policy: choke in the policy's order until plant limits hold "
        "and all gas has a buyer."
    ),
    "greedy baseline": "Spreadsheet-style incumbent: POS order fill, then highest-CO2 choke-back.",
    "paired comparison": "Every policy replayed on the same true day from the same starting rates.",
    "bootstrap CI": "Confidence interval from resampling daily differences 2,000 times (5th-95th percentile).",
    "uplift": "Advisor value minus baseline value per day (USD/d), and as % of baseline.",
    "violation-day": "A day on which a policy breached at least one hard limit after the guard.",
}

_GLOSSARY_INDEX: dict[str, str] = {
    alias.strip().casefold(): key for key in GLOSSARY for alias in (key, *key.split(" / "))
}


def lookup(term: str) -> str | None:
    """Definition of ``term``, matched case-insensitively on a glossary key or any of its ' / ' parts."""
    key = _GLOSSARY_INDEX.get(term.strip().casefold())
    return GLOSSARY[key] if key is not None else None
