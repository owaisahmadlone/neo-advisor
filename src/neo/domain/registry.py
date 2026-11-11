"""Engineering and commercial registers: the static, effective-dated description of the network.

These are parsed from YAML at the boundary (pydantic) and are read-only afterwards. In a
deployment they come from PI Asset Framework, engineering files, the GSA register and the
limits register; here they come from ``configs/reference``.
"""

from __future__ import annotations

from datetime import date
from functools import cached_property
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from neo.core.gasday import GasDayCalendar
from neo.physics.blend import Composition
from neo.physics.compressor import FuelModel, TrainMap
from neo.physics.hydrate import HydrateCurve
from neo.physics.pipes import HoldupModel, PipeHydraulics, line_volume_bbl, weymouth_coefficient
from neo.physics.wells import WellheadCurve

PressureRole = Literal["setpoint", "free"]
NodeKind = Literal["manifold", "hub_suction", "hub_discharge", "arrival"]


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Node(Frozen):
    id: str
    kind: NodeKind
    pressure_role: PressureRole
    maop_bara: float
    p_min_bara: float | None = None
    p_max_bara: float | None = None
    setpoint_bara: float | None = None


class Pipe(Frozen):
    id: str
    from_node: str
    to_node: str
    length_km: float
    diameter_in: float
    efficiency: float
    gas_sg: float = 0.68
    t_avg_c: float = 28.0
    z_avg: float = 0.88
    liquid_head_bar2: float = 0.0
    holdup_beta: float = 0.0
    elevation_s: float = 0.0
    capacity_mmscfd: float
    parallel_group: str | None = None
    holdup_max: float = 0.0
    inventory_beta: float = 0.0

    @cached_property
    def hydraulics(self) -> PipeHydraulics:
        k = weymouth_coefficient(
            length_km=self.length_km,
            diameter_in=self.diameter_in,
            efficiency=self.efficiency,
            gas_sg=self.gas_sg,
            t_avg_c=self.t_avg_c,
            z_avg=self.z_avg,
        )
        return PipeHydraulics(k, self.liquid_head_bar2, self.holdup_beta, self.elevation_s)

    @cached_property
    def holdup(self) -> HoldupModel | None:
        if self.holdup_max <= 0:
            return None
        volume = line_volume_bbl(length_km=self.length_km, diameter_in=self.diameter_in)
        return HoldupModel(volume, self.holdup_max, self.inventory_beta)


class HydrateParams(Frozen):
    a: float
    b: float

    @property
    def curve(self) -> HydrateCurve:
        return HydrateCurve(self.a, self.b)


class Platform(Frozen):
    id: str
    manifold: str
    export_options: list[str] = Field(min_length=1)
    water_cap_bbld: float
    opex_usd_per_mmscf: float
    hydrate: HydrateParams


class Well(Frozen):
    id: str
    platform: str
    psc: str
    c_wh: float
    n_wh: float
    p_ws_bara: float
    p_ws_decline_bar_per_day: float = 0.0
    rate_cap_mmscfd: float
    min_rate_mmscfd: float
    tubing_id_in: float
    wellhead_temp_c: float
    choke_bore_in: float
    cgr_bbl_mmscf: float
    wgr_bbl_mmscf: float
    co2: float
    h2s_ppmv: float
    hg_ug_sm3: float
    ghv_hc_btu_scf: float

    @property
    def composition(self) -> Composition:
        raw_ghv = (1 - self.co2) * self.ghv_hc_btu_scf
        return Composition(self.co2, self.h2s_ppmv, self.hg_ug_sm3, raw_ghv)

    @property
    def curve(self) -> WellheadCurve:
        return WellheadCurve(self.c_wh, self.n_wh, self.p_ws_bara)


class TrainSpec(Frozen):
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

    @property
    def map(self) -> TrainMap:
        return TrainMap(**self.model_dump())


class FuelSpec(Frozen):
    thermal_eff: float
    lhv_btu_scf: float
    idle_power_kw: float = 0.0

    @property
    def model(self) -> FuelModel:
        return FuelModel(**self.model_dump())


class CompressorStation(Frozen):
    id: str
    suction_node: str
    discharge_node: str
    trains: int
    train: TrainSpec
    fuel: FuelSpec
    ps_min_bara: float
    pd_max_bara: float
    design_ambient_c: float = 30.0


class ThirdPartyStream(Frozen):
    id: str
    node: str
    nominal_rate_mmscfd: float
    co2: float
    h2s_ppmv: float
    hg_ug_sm3: float
    ghv_hc_btu_scf: float
    cgr_bbl_mmscf: float
    wgr_bbl_mmscf: float

    @property
    def composition(self) -> Composition:
        return Composition(self.co2, self.h2s_ppmv, self.hg_ug_sm3, (1 - self.co2) * self.ghv_hc_btu_scf)


class SlugCatcher(Frozen):
    node: str
    liquid_cap_bbld: float
    free_volume_bbl: float
    sweep_fraction: float = 0.8


class SalesSpec(Frozen):
    co2_max: float
    ghv_min_btu_scf: float
    ghv_max_btu_scf: float
    h2s_max_ppmv: float
    hg_max_ug_sm3: float


class Plant(Frozen):
    id: str
    inlet_node: str
    inlet_cap_mmscfd: float
    co2_removal_cap_mmscfd: float
    shrink_frac: float
    spec: SalesSpec


class MegSystem(Frozen):
    cap_bbld: float
    lean_wt: float
    seabed_temp_c: float
    hydrate_margin_c: float


class Contract(Frozen):
    id: str
    buyer: str
    kind: Literal["gsa", "excess"]
    price_usd_mmbtu: float
    priority: int = 1
    dcq_mmbtu_d: float = 0.0
    mdq_mmbtu_d: float = 0.0
    shortfall_usd_mmbtu: float = 0.0
    cap_mmbtu_d: float = 0.0


class PscBlock(Frozen):
    id: str
    term_type: str
    expiry: date
    netback_factor: float = Field(gt=0, le=1)
    pos_rank: int


class Network(Frozen):
    """The asset registry: topology, equipment and approved limits."""

    name: str
    version: str
    valid_from: date
    gas_day: GasDayCalendarSpec
    nodes: list[Node]
    platforms: list[Platform]
    wells: list[Well]
    pipes: list[Pipe]
    compressor: CompressorStation
    third_party: list[ThirdPartyStream]
    slug_catcher: SlugCatcher
    plant: Plant
    meg: MegSystem

    @cached_property
    def node_by_id(self) -> dict[str, Node]:
        return {n.id: n for n in self.nodes}

    @cached_property
    def pipe_by_id(self) -> dict[str, Pipe]:
        return {p.id: p for p in self.pipes}

    @cached_property
    def platform_by_id(self) -> dict[str, Platform]:
        return {p.id: p for p in self.platforms}

    @cached_property
    def well_by_id(self) -> dict[str, Well]:
        return {w.id: w for w in self.wells}

    def wells_on(self, platform_id: str) -> list[Well]:
        return [w for w in self.wells if w.platform == platform_id]

    @property
    def arrival_node(self) -> Node:
        return next(n for n in self.nodes if n.kind == "arrival")

    @property
    def trunk_pipes(self) -> list[Pipe]:
        arrival = self.arrival_node.id
        return [p for p in self.pipes if p.to_node == arrival]

    @property
    def calendar(self) -> GasDayCalendar:
        return GasDayCalendar(**self.gas_day.model_dump())

    @model_validator(mode="after")
    def _references_exist(self) -> Network:
        node_ids = {n.id for n in self.nodes}
        pipe_ids = {p.id for p in self.pipes}
        platform_ids = {p.id for p in self.platforms}
        problems = [
            *(f"pipe {p.id}: unknown node" for p in self.pipes if {p.from_node, p.to_node} - node_ids),
            *(f"well {w.id}: unknown platform" for w in self.wells if w.platform not in platform_ids),
            *(f"platform {p.id}: unknown export pipe" for p in self.platforms if set(p.export_options) - pipe_ids),
        ]
        if problems:
            raise ValueError("; ".join(problems))
        return self


class GasDayCalendarSpec(Frozen):
    timezone: str = "Asia/Kuala_Lumpur"
    start_hour: int = Field(6, ge=0, le=23)
    nomination_cutoff_hour: int = Field(11, ge=0, le=23)


class Commercial(Frozen):
    """GSA register and price assumptions."""

    condensate_usd_bbl: float
    contracts: list[Contract]

    @property
    def gsas(self) -> list[Contract]:
        return [c for c in self.contracts if c.kind == "gsa"]

    @property
    def excess_outlets(self) -> list[Contract]:
        return [c for c in self.contracts if c.kind == "excess"]


class PscRegister(Frozen):
    blocks: list[PscBlock]

    @cached_property
    def by_id(self) -> dict[str, PscBlock]:
        return {b.id: b for b in self.blocks}


class Registry(Frozen):
    """Everything static the advisor reads: network, commercial terms, PSC terms."""

    network: Network
    commercial: Commercial
    psc: PscRegister

    @classmethod
    def load(cls, directory: Path) -> Registry:
        def read(name: str) -> dict:
            return yaml.safe_load((directory / name).read_text())

        return cls(
            network=Network.model_validate(read("asset_registry.yaml")),
            commercial=Commercial.model_validate(read("contracts.yaml")),
            psc=PscRegister.model_validate(read("psc_terms.yaml")),
        )


Network.model_rebuild()
