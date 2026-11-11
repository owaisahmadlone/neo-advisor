"""Nonlinear forward simulation of a gas-day plan.

One implementation of the steady-state physics serves two callers:

* the advisor's verifier ("check" mode): rates are taken as planned; pressures are
  recomputed with the exact (Weymouth + Flanigan) equalities working back from the
  set-point nodes, and every hard limit is checked in fixed code;
* the synthetic truth plant ("respond" mode): rates are targets; each well delivers at
  most what its true deliverability allows at the actual manifold pressure, and suction
  pressure rises if the compressor cannot take the inflow.

The caller decides which parameters it passes in (calibrated vs. true).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from scipy.optimize import brentq

from neo.domain.registry import Network
from neo.physics import choke
from neo.physics.blend import Composition, blend, co2_removal_needed, plant_outlet
from neo.physics.compressor import FuelModel, TrainMap, station_fuel_mmscfd
from neo.physics.hydrate import meg_injection_per_bbl_water, required_subcooling_c
from neo.physics.pipes import HoldupModel, PipeHydraulics

Mode = Literal["check", "respond"]
TOL = 1e-6


class WellModel(Protocol):
    def rate(self, p_wh: float) -> float: ...
    def wellhead_pressure(self, q: float) -> float: ...


@dataclass(frozen=True, slots=True)
class WellPhysics:
    model: WellModel
    platform: str
    composition: Composition
    cgr: float
    wgr: float
    wellhead_temp_c: float
    choke_bore_in: float


@dataclass(frozen=True, slots=True)
class PhysicalNetwork:
    """Everything a forward simulation needs, with one parameter set (calibrated or true)."""

    network: Network
    wells: Mapping[str, WellPhysics]
    pipes: Mapping[str, PipeHydraulics]
    holdups: Mapping[str, HoldupModel]
    train: TrainMap
    fuel: FuelModel
    ambient_c: float
    third_party: Mapping[str, tuple[float, Composition, float, float]]  # rate, comp, cgr, wgr
    plant_inlet_cap: float
    co2_removal_cap: float
    condensate_headroom: float


@dataclass(frozen=True, slots=True)
class Setpoints:
    rates: Mapping[str, float]
    routes: Mapping[str, str]
    trains: int
    suction_bara: float


@dataclass(frozen=True, slots=True)
class Violation:
    limit: str
    subject: str
    value: float
    bound: float
    severity: Literal["hard", "warning"] = "hard"

    def __str__(self) -> str:
        return f"{self.limit}[{self.subject}]: {self.value:.3f} vs {self.bound:.3f}"


@dataclass(frozen=True, slots=True)
class WellOutcome:
    rate: float
    manifold_bara: float
    wellhead_bara: float
    choke_dp_bar: float
    choke_opening_pct: float
    downstream_temp_c: float


@dataclass(frozen=True, slots=True)
class Outcome:
    setpoints: Setpoints
    wells: Mapping[str, WellOutcome]
    pressures: Mapping[str, float]
    arc_flow: Mapping[str, float]
    hub_inflow: float
    fuel: float
    plant_inlet: float
    inlet_composition: Composition
    co2_removal_needed: float
    sales_volume: float
    sales_energy_total: float
    sales_ghv: float
    sales_co2: float
    our_energy: float
    condensate: float
    our_condensate_by_well: Mapping[str, float]
    water: float
    violations: tuple[Violation, ...] = field(default_factory=tuple)

    @property
    def hard_violations(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.severity == "hard")


def _platform_flows(phys: PhysicalNetwork, rates: Mapping[str, float]) -> dict[str, float]:
    flows = {p.id: 0.0 for p in phys.network.platforms}
    for well_id, q in rates.items():
        flows[phys.wells[well_id].platform] += q
    return flows


def _natural_split(pipes: list[tuple[str, PipeHydraulics]], total: float) -> dict[str, float]:
    if len(pipes) == 1:
        return {pipes[0][0]: total}
    (id1, h1), (id2, h2) = pipes[:2]

    def gap(q1: float) -> float:
        return h1.dp2(q1) - h2.dp2(total - q1)

    if total <= 0:
        return {id1: 0.0, id2: 0.0}
    if gap(0.0) * gap(total) < 0:
        q1 = brentq(gap, 0.0, total, xtol=1e-9)
    else:
        w1, w2 = 1 / math.sqrt(h1.k_friction), 1 / math.sqrt(h2.k_friction)
        q1 = total * w1 / (w1 + w2)
    return {id1: q1, id2: total - q1}


def simulate(
    phys: PhysicalNetwork,
    setpoints: Setpoints,
    *,
    mode: Mode = "check",
    current_rates: Mapping[str, float] | None = None,
    move_limits: Mapping[str, float] | None = None,
) -> Outcome:
    net = phys.network
    comp = net.compressor
    rates = {w: max(q, 0.0) for w, q in setpoints.rates.items()}
    suction = setpoints.suction_bara
    for _ in range(60):
        state = _solve_pressures(phys, setpoints, rates, suction)
        if mode == "check":
            break
        delivered = {}
        for well_id, target in setpoints.rates.items():
            if target <= 0:
                delivered[well_id] = 0.0
                continue
            p_man = state["pressures"][net.platform_by_id[phys.wells[well_id].platform].manifold]
            delivered[well_id] = min(target, phys.wells[well_id].model.rate(p_man))
        required = _required_suction(phys, setpoints, state["hub_inflow"], state["pressures"][comp.discharge_node])
        settled = all(abs(delivered[w] - rates[w]) < 1e-4 for w in rates) and abs(required - suction) < 1e-4
        rates = {w: 0.5 * rates[w] + 0.5 * delivered[w] for w in rates}
        suction = 0.5 * suction + 0.5 * required
        if settled:
            break
    state = _solve_pressures(phys, setpoints, rates, suction)
    return _assemble(phys, setpoints, rates, suction, state, current_rates, move_limits)


def _required_suction(phys: PhysicalNetwork, sp: Setpoints, inflow: float, discharge: float) -> float:
    """Lowest suction pressure, not below the set-point, at which the running trains take the inflow.

    Capacity rises with suction pressure, so when the machines cannot keep up the suction
    floats up until they can; when they can, it sits at the set-point."""
    if sp.trains == 0:
        return sp.suction_bara

    def capacity(ps: float) -> float:
        return sp.trains * phys.train.capacity_mmscfd(ps, discharge, phys.ambient_c)

    lo, hi = sp.suction_bara, min(discharge, 4 * sp.suction_bara)
    if capacity(lo) >= inflow or capacity(hi) < inflow:
        return lo if capacity(lo) >= inflow else hi
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        lo, hi = (lo, mid) if capacity(mid) >= inflow else (mid, hi)
    return hi


def _solve_pressures(phys: PhysicalNetwork, sp: Setpoints, rates: Mapping[str, float], suction: float) -> dict:
    net = phys.network
    comp = net.compressor
    platform_flow = _platform_flows(phys, rates)
    arc_flow: dict[str, float] = {}
    for platform in net.platforms:
        chosen = sp.routes.get(platform.id, platform.export_options[0])
        for pipe_id in platform.export_options:
            arc_flow[pipe_id] = platform_flow[platform.id] if pipe_id == chosen else 0.0
    third_party_rate = sum(r for r, *_ in phys.third_party.values())
    hub_inflow = third_party_rate + sum(
        q for pid, q in arc_flow.items() if net.pipe_by_id[pid].to_node == comp.suction_node
    )
    bypass = sum(q for pid, q in arc_flow.items() if net.pipe_by_id[pid].to_node == comp.discharge_node)
    arrival = net.arrival_node.setpoint_bara
    trunks = [(p.id, phys.pipes[p.id]) for p in net.trunk_pipes]
    discharge, fuel = arrival * 1.2, 0.0
    for _ in range(8):
        fuel = station_fuel_mmscfd(
            phys.train, phys.fuel, q_mmscfd=hub_inflow, trains=sp.trains, ps=suction, pd=discharge
        )
        total = hub_inflow - fuel + bypass
        split = _natural_split(trunks, total)
        first_id, first = trunks[0]
        discharge = first.inlet_pressure(arrival, split[first_id])
    arc_flow.update(split)
    pressures = {net.arrival_node.id: arrival, comp.discharge_node: discharge, comp.suction_node: suction}
    for platform in net.platforms:
        chosen = sp.routes.get(platform.id, platform.export_options[0])
        pipe = net.pipe_by_id[chosen]
        pressures[platform.manifold] = phys.pipes[chosen].inlet_pressure(pressures[pipe.to_node], arc_flow[chosen])
    return {
        "arc_flow": arc_flow,
        "hub_inflow": hub_inflow,
        "fuel": fuel,
        "trunk_total": total,
        "pressures": pressures,
    }


def _assemble(
    phys: PhysicalNetwork,
    sp: Setpoints,
    rates: Mapping[str, float],
    suction: float,
    state: dict,
    current_rates: Mapping[str, float] | None,
    move_limits: Mapping[str, float] | None,
) -> Outcome:
    net = phys.network
    comp, plant, spec = net.compressor, net.plant, net.plant.spec
    pressures, arc_flow = state["pressures"], state["arc_flow"]
    violations: list[Violation] = []

    def check(
        limit: str, subject: str, value: float, bound: float, *, upper: bool = True, severity: str = "hard"
    ) -> None:
        breached = value > bound * (1 + 1e-6) + TOL if upper else value < bound * (1 - 1e-6) - TOL
        if breached:
            violations.append(Violation(limit, subject, value, bound, severity))

    well_outcomes = {}
    for well_id, q in rates.items():
        wp = phys.wells[well_id]
        platform = net.platform_by_id[wp.platform]
        p_man = pressures[platform.manifold]
        if q <= 0:
            well_outcomes[well_id] = WellOutcome(0.0, p_man, math.nan, 0.0, 0.0, wp.wellhead_temp_c)
            continue
        check("well_deliverability", well_id, q, wp.model.rate(p_man))
        p_wh = wp.model.wellhead_pressure(q)
        dp = p_wh - p_man if not math.isnan(p_wh) else math.nan
        area = math.pi / 4 * (wp.choke_bore_in * 0.0254) ** 2
        opening = (
            choke.opening_pct(q, p_up=p_wh, p_dn=p_man, max_area_m2=area, t_up_c=wp.wellhead_temp_c)
            if dp > 0
            else math.inf
        )
        t_down = choke.jt_downstream_temperature_c(wp.wellhead_temp_c, dp if dp > 0 else 0.0)
        hydrate_t = platform.hydrate.curve.temperature_c(p_man)
        check("choke_hydrate_margin", well_id, hydrate_t + net.meg.hydrate_margin_c, t_down, severity="warning")
        well_outcomes[well_id] = WellOutcome(q, p_man, p_wh, dp, opening, t_down)

    for node in net.nodes:
        if node.id in pressures:
            check("maop", node.id, pressures[node.id], node.maop_bara)
    for pipe in net.pipes:
        check("pipe_capacity", pipe.id, arc_flow.get(pipe.id, 0.0), pipe.capacity_mmscfd)

    hub, ps, pd = state["hub_inflow"], suction, pressures[comp.discharge_node]
    if sp.trains > 0:
        check("compressor_capacity", comp.id, hub, sp.trains * phys.train.capacity_mmscfd(ps, pd, phys.ambient_c))
        check("compressor_surge", comp.id, hub, sp.trains * phys.train.surge_min_mmscfd(ps), upper=False)
        check("compressor_ratio", comp.id, pd / ps, phys.train.ratio_max)
    else:
        check("compressor_capacity", comp.id, hub, 0.0)
    check("discharge_pressure", comp.id, pd, comp.pd_max_bara)
    check("suction_pressure", comp.id, ps, comp.ps_min_bara, upper=False)

    # Quality: fuel is drawn from the hub mix, so it removes volume but not composition.
    hub_streams = [(r, c) for r, c, *_ in phys.third_party.values()]
    bypass_streams = []
    for well_id, q in rates.items():
        wp = phys.wells[well_id]
        chosen = sp.routes.get(wp.platform, net.platform_by_id[wp.platform].export_options[0])
        target = hub_streams if net.pipe_by_id[chosen].to_node == comp.suction_node else bypass_streams
        target.append((q, wp.composition))
    hub_q, hub_mix = blend(hub_streams)
    inlet_q, inlet_mix = blend([(max(hub_q - state["fuel"], 0.0), hub_mix), *bypass_streams])
    removal = co2_removal_needed(q_in=inlet_q, inlet=inlet_mix, co2_max=spec.co2_max, shrink_frac=plant.shrink_frac)
    sales = plant_outlet(
        q_in=inlet_q, inlet=inlet_mix, co2_removed=min(removal, phys.co2_removal_cap), shrink_frac=plant.shrink_frac
    )
    check("plant_inlet", plant.id, inlet_q, phys.plant_inlet_cap)
    check("co2_removal", plant.id, removal, phys.co2_removal_cap)
    check("sales_co2", plant.id, sales.co2, spec.co2_max)
    if sales.volume_mmscfd > 0:
        check("sales_ghv_min", plant.id, sales.ghv_btu_scf, spec.ghv_min_btu_scf, upper=False)
        check("sales_ghv_max", plant.id, sales.ghv_btu_scf, spec.ghv_max_btu_scf)
    check("h2s_inlet", plant.id, inlet_mix.h2s_ppmv, spec.h2s_max_ppmv)
    check("hg_inlet", plant.id, inlet_mix.hg_ug_sm3, spec.hg_max_ug_sm3)

    our_cond = {w: q * phys.wells[w].cgr for w, q in rates.items()}
    third_cond = sum(r * cgr for r, _, cgr, _ in phys.third_party.values())
    water = sum(q * phys.wells[w].wgr for w, q in rates.items()) + sum(
        r * wgr for r, _, _, wgr in phys.third_party.values()
    )
    condensate = sum(our_cond.values()) + third_cond
    sc = net.slug_catcher
    check("slug_liquid", sc.node, condensate + water, sc.liquid_cap_bbld)
    check("terminal_headroom", "terminal", condensate, phys.condensate_headroom)
    for platform in net.platforms:
        platform_water = sum(q * phys.wells[w].wgr for w, q in rates.items() if phys.wells[w].platform == platform.id)
        check("platform_water", platform.id, platform_water, platform.water_cap_bbld)
    curve = max((p.hydrate.curve for p in net.platforms), key=lambda c: c.temperature_c(pd))
    subcooling = required_subcooling_c(
        curve, p_bara=pd, t_min_c=net.meg.seabed_temp_c, margin_c=net.meg.hydrate_margin_c
    )
    check("meg", "MEG", water * meg_injection_per_bbl_water(subcooling, lean_meg_wt=net.meg.lean_wt), net.meg.cap_bbld)
    if current_rates is not None:
        _check_sweep(phys, arc_flow, current_rates, sp, check)
    if current_rates is not None and move_limits is not None:
        for well_id, q in rates.items():
            check(
                "move_limit",
                well_id,
                abs(q - current_rates.get(well_id, 0.0)),
                move_limits[well_id],
                severity="warning",
            )

    third_energy = sum(r * c.ghv_btu_scf for r, c, *_ in phys.third_party.values())
    our_raw = sum(q * phys.wells[w].composition.ghv_btu_scf for w, q in rates.items())
    our_share = our_raw / (our_raw + third_energy) if our_raw + third_energy > 0 else 0.0
    return Outcome(
        setpoints=sp,
        wells=well_outcomes,
        pressures=dict(pressures),
        arc_flow=dict(arc_flow),
        hub_inflow=hub,
        fuel=state["fuel"],
        plant_inlet=inlet_q,
        inlet_composition=inlet_mix,
        co2_removal_needed=removal,
        sales_volume=sales.volume_mmscfd,
        sales_energy_total=sales.energy_mmbtu_d,
        sales_ghv=sales.ghv_btu_scf,
        sales_co2=sales.co2,
        our_energy=sales.energy_mmbtu_d * our_share,
        condensate=condensate,
        our_condensate_by_well=our_cond,
        water=water,
        violations=tuple(violations),
    )


def _check_sweep(phys: PhysicalNetwork, arc_flow, current_rates, sp, check) -> None:
    trunks = [p.id for p in phys.network.trunk_pipes if p.id in phys.holdups]
    if not trunks:
        return
    current = simulate(phys, Setpoints(current_rates, sp.routes, sp.trains, sp.suction_bara))
    released = sum(
        phys.holdups[t].inventory_bbl(current.arc_flow[t]) - phys.holdups[t].inventory_bbl(arc_flow[t]) for t in trunks
    )
    sc = phys.network.slug_catcher
    check("slug_sweep", sc.node, released, sc.sweep_fraction * sc.free_volume_bbl)
