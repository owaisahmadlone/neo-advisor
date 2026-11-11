"""Historian tag catalogue for a network, and the true value of every tag in a steady period."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from neo.core.units import bara_to_barg
from neo.domain.registry import Network
from neo.ingest.schema import NETWORK_TAGS, well_tag
from neo.synth.truth import PeriodResult
from neo.verify.forward import WellOutcome

TagKind = Literal["pressure", "temperature", "flow", "fiscal", "vfm", "analyser", "heating_value", "choke", "digital"]
ANALOG_KINDS = frozenset({"pressure", "temperature", "flow", "fiscal", "vfm", "analyser", "heating_value", "choke"})

WELL_TAG_SPECS: dict[str, tuple[str, TagKind]] = {
    "PWH": ("barg", "pressure"),
    "TWH": ("degC", "temperature"),
    "CHOKE": ("%", "choke"),
    "VFM_GAS": ("MMscfd", "vfm"),
    "STATUS": ("bool", "digital"),
}


@dataclass(frozen=True, slots=True)
class TagSpec:
    """One PI point. The tag is ``<asset>.<attribute>``; the AF path nests the asset under an area."""

    tag: str
    area: str
    uom: str
    kind: TagKind
    critical: bool = False

    @property
    def asset_id(self) -> str:
        return self.tag.rsplit(".", 1)[0]

    @property
    def attribute(self) -> str:
        return self.tag.rsplit(".", 1)[1]

    @property
    def af_path(self) -> str:
        element = self.asset_id.replace(".", "\\")
        return f"\\\\NeO\\{self.area}\\{element}|{self.attribute}"

    @property
    def analog(self) -> bool:
        return self.kind in ANALOG_KINDS


def train_tags(trains: int) -> list[str]:
    return [NETWORK_TAGS["train_running"].format(n=n) for n in range(1, trains + 1)]


def tag_catalogue(net: Network) -> tuple[TagSpec, ...]:
    comp = net.compressor
    area_of_node = {p.manifold: p.id for p in net.platforms} | {comp.suction_node: "HUB", comp.discharge_node: "HUB"}
    critical_pressures = {NETWORK_TAGS[k] for k in ("suction_pressure", "discharge_pressure", "arrival_pressure")}
    specs = [
        TagSpec(well_tag(w.id, attribute), w.platform, uom, kind)
        for w in net.wells
        for attribute, (uom, kind) in WELL_TAG_SPECS.items()
    ]
    for node in net.nodes:
        tag = NETWORK_TAGS["manifold_pressure"].format(node=node.id)
        specs.append(TagSpec(tag, area_of_node.get(node.id, node.id), "barg", "pressure", tag in critical_pressures))
    specs += [
        TagSpec(NETWORK_TAGS["hub_inlet_flow"], "HUB", "MMscfd", "flow", critical=True),
        *(TagSpec(tag, "HUB", "bool", "digital", critical=True) for tag in train_tags(comp.trains)),
        *(
            TagSpec(NETWORK_TAGS["third_party_flow"].format(stream=s.id), "HUB", "MMscfd", "flow", critical=True)
            for s in net.third_party
        ),
        *(TagSpec(NETWORK_TAGS["trunk_flow"].format(pipe=p.id), "OSC", "MMscfd", "flow") for p in net.trunk_pipes),
        TagSpec(NETWORK_TAGS["plant_inlet_flow"], "GPP", "MMscfd", "flow"),
        TagSpec(NETWORK_TAGS["plant_inlet_co2"], "GPP", "mol%", "analyser", critical=True),
        TagSpec(NETWORK_TAGS["sales_flow"], "GPP", "MMscfd", "fiscal"),
        TagSpec(NETWORK_TAGS["sales_ghv"], "GPP", "Btu/scf", "heating_value"),
        TagSpec(NETWORK_TAGS["ambient_temperature"], "HUB", "degC", "temperature"),
    ]
    return tuple(sorted(specs, key=lambda s: s.tag))


def flowing_wellhead_bara(well: WellOutcome) -> float:
    """A well flowing at its deliverability has its choke wide open: p_wh equals the manifold."""
    return well.manifold_bara if math.isnan(well.wellhead_bara) else well.wellhead_bara


def choke_pct(well: WellOutcome) -> float:
    return min(well.choke_opening_pct, 100.0)


def period_values(period: PeriodResult) -> dict[str, float]:
    """True value of every tag while ``period`` lasts (pressures in barg, as historians record)."""
    phys, out = period.physical, period.outcome
    net = phys.network
    values: dict[str, float] = {}
    for well_id, physics in phys.wells.items():
        well = out.wells[well_id]
        flowing = well.rate > 0
        p_wh = flowing_wellhead_bara(well) if flowing else physics.model.wellhead_pressure(0.0)
        values[well_tag(well_id, "PWH")] = bara_to_barg(p_wh)
        values[well_tag(well_id, "TWH")] = physics.wellhead_temp_c if flowing else phys.ambient_c
        values[well_tag(well_id, "CHOKE")] = choke_pct(well) if flowing else 0.0
        values[well_tag(well_id, "VFM_GAS")] = well.rate
        values[well_tag(well_id, "STATUS")] = float(flowing)
    for node_id, pressure in out.pressures.items():
        values[NETWORK_TAGS["manifold_pressure"].format(node=node_id)] = bara_to_barg(pressure)
    values[NETWORK_TAGS["hub_inlet_flow"]] = out.hub_inflow
    for n, tag in enumerate(train_tags(net.compressor.trains), start=1):
        values[tag] = float(n <= period.setpoints.trains)
    for stream_id, (rate, *_) in phys.third_party.items():
        values[NETWORK_TAGS["third_party_flow"].format(stream=stream_id)] = rate
    for pipe in net.trunk_pipes:
        values[NETWORK_TAGS["trunk_flow"].format(pipe=pipe.id)] = out.arc_flow[pipe.id]
    values[NETWORK_TAGS["plant_inlet_flow"]] = out.plant_inlet
    values[NETWORK_TAGS["plant_inlet_co2"]] = 100 * out.inlet_composition.co2
    values[NETWORK_TAGS["sales_flow"]] = out.sales_volume
    values[NETWORK_TAGS["sales_ghv"]] = out.sales_ghv
    values[NETWORK_TAGS["ambient_temperature"]] = phys.ambient_c
    return values
