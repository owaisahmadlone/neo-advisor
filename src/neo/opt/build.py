"""Build the gas-day planning model.

One builder serves four lexicographic stages and two modes:

* stages: ``restore`` (min elastic move-limit slack), ``deliver`` (min priority-weighted
  shortfall), ``value`` (max enterprise value, near the current point) and ``rank``
  (the destination plan: max value minus monetary shortfall, no move limits). The
  ``rank`` stage's duals give the merit order.
* modes: MILP when ``fixed`` is None (well on/off, platform routing and train count are
  decisions), LP when ``fixed`` is given (integers fixed, so duals exist).

Pressures enter as u = P^2 (bara^2), which makes Weymouth-type drops and wellhead
deliverability linear after conservative chords (see ``opt.pwl``). Arc flows are
expressions of well rates, so a row's coefficient on a well's rate is that well's use of
the limit along its path.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from neo.domain.registry import Pipe, Registry, Well
from neo.domain.state import NetworkState
from neo.opt.lp import INF, Expr, LinearModel
from neo.opt.pwl import Plane, chord_lines, conservative_planes

Stage = Literal["restore", "deliver", "value", "rank"]


@dataclass(frozen=True, slots=True)
class ModelConfig:
    well_pwl_tol: float = 0.05
    pipe_pwl_tol: float = 6.0
    move_abs_mmscfd: float = 6.0
    move_rel: float = 0.25
    swing_usd_per_mmscfd: float = 120.0
    capacity_backoff: float = 0.02
    deliverability_k: float = 0.0
    max_deliverability_backoff: float = 0.25
    compressor_bands: int = 4
    restore_eps: float = 1e-6
    shortfall_eps: float = 1e-3


@dataclass(frozen=True, slots=True)
class DiscretePlan:
    wells_on: frozenset[str]
    routes: Mapping[str, str]
    trains: int

    def with_well(self, well_id: str, *, on: bool) -> DiscretePlan:
        wells = self.wells_on | {well_id} if on else self.wells_on - {well_id}
        return DiscretePlan(frozenset(wells), self.routes, self.trains)


@dataclass(frozen=True, slots=True)
class StageCaps:
    sigma: float | None = None
    weighted_shortfall: float | None = None


@dataclass(slots=True)
class PlanModel:
    model: LinearModel
    stage: Stage
    rate: dict[str, Expr] = field(default_factory=dict)
    on: dict[str, Expr] = field(default_factory=dict)
    u: dict[str, Expr] = field(default_factory=dict)
    route: dict[tuple[str, str], Expr] = field(default_factory=dict)
    train_choice: dict[int, Expr] = field(default_factory=dict)
    arc_flow: dict[str, Expr] = field(default_factory=dict)
    trains: Expr = field(default_factory=Expr)
    hub_inflow: Expr = field(default_factory=Expr)
    trunk_total: Expr = field(default_factory=Expr)
    suction_u: Expr = field(default_factory=Expr)
    discharge_u: Expr = field(default_factory=Expr)
    fuel: Expr = field(default_factory=Expr)
    co2_removed: Expr = field(default_factory=Expr)
    sales_volume: Expr = field(default_factory=Expr)
    sales_energy_total: Expr = field(default_factory=Expr)
    condensate: Expr = field(default_factory=Expr)
    water: Expr = field(default_factory=Expr)
    delivered: dict[str, Expr] = field(default_factory=dict)
    excess: dict[str, Expr] = field(default_factory=dict)
    shortfall: dict[str, Expr] = field(default_factory=dict)
    weighted_shortfall: Expr = field(default_factory=Expr)
    sigma_total: Expr = field(default_factory=Expr)
    moves: Expr = field(default_factory=Expr)
    value_parts: dict[str, Expr] = field(default_factory=dict)
    planes: list[Plane] = field(default_factory=list)

    @property
    def value(self) -> Expr:
        return Expr.total(self.value_parts.values())


def priority_weight(priority: int) -> float:
    return 10.0 ** (1 - priority)


def deliverability_backoff(sigma_rel: float, cfg: ModelConfig) -> float:
    return min(cfg.deliverability_k * sigma_rel, cfg.max_deliverability_backoff)


class _Builder:
    def __init__(
        self,
        registry: Registry,
        state: NetworkState,
        cfg: ModelConfig,
        stage: Stage,
        fixed: DiscretePlan | None,
        caps: StageCaps,
    ) -> None:
        self.reg = registry
        self.net = registry.network
        self.state = state
        self.cfg = cfg
        self.stage = stage
        self.fixed = fixed
        self.caps = caps
        sense = "min" if stage in ("restore", "deliver") else "max"
        self.pm = PlanModel(model=LinearModel(sense), stage=stage)
        self.m = self.pm.model
        self.train_map = self.net.compressor.train.map
        self.fuel_model = self.net.compressor.fuel.model

    # ----------------------------------------------------------------- helpers
    def _is_fixed(self) -> bool:
        return self.fixed is not None

    def _bo(self, value: float) -> float:
        return value * (1 - self.cfg.capacity_backoff)

    def _available(self, well: Well) -> bool:
        return self.state.wells[well.id].available

    # ------------------------------------------------------------------ build
    def build(self) -> PlanModel:
        self._nodes()
        self._wells()
        self._routing()
        self._compressor()
        self._trunks()
        self._pipes()
        self._plant()
        self._liquids()
        self._contracts()
        if self.stage != "rank":
            self._moves()
        self._objective()
        return self.pm

    def _nodes(self) -> None:
        net, comp = self.net, self.net.compressor
        hub_suction = net.node_by_id[comp.suction_node]
        for node in net.nodes:
            if node.kind == "arrival":
                self.pm.u[node.id] = Expr({}, node.setpoint_bara**2)
            elif node.pressure_role == "setpoint":
                self.pm.u[node.id] = self.m.var(
                    f"u[{node.id}]", node.p_min_bara**2, min(node.p_max_bara, node.maop_bara) ** 2
                )
            else:
                floor = net.arrival_node.setpoint_bara if node.kind == "hub_discharge" else hub_suction.p_min_bara
                self.pm.u[node.id] = self.m.var(f"u[{node.id}]", floor**2, node.maop_bara**2)

    def _wells(self) -> None:
        for well in self.net.wells:
            ws = self.state.wells[well.id]
            cap = well.rate_cap_mmscfd
            if not ws.available or (self.fixed and well.id not in self.fixed.wells_on):
                self.pm.rate[well.id] = self.m.var(f"q[{well.id}]", 0.0, 0.0)
                self.pm.on[well.id] = Expr({}, 0.0)
                continue
            if self._is_fixed():
                q = self.m.var(f"q[{well.id}]", well.min_rate_mmscfd, cap)
                on = Expr({}, 1.0)
            else:
                q = self.m.var(f"q[{well.id}]", 0.0, cap)
                on = self.m.binary(f"y[{well.id}]")
                self.m.le(
                    f"status_max[{well.id}]", q, on * cap, kind="well_status", shared=False, meta={"well": well.id}
                )
                self.m.ge(
                    f"status_min[{well.id}]",
                    q,
                    on * well.min_rate_mmscfd,
                    kind="well_status",
                    shared=False,
                    meta={"well": well.id},
                )
            self.pm.rate[well.id] = q
            self.pm.on[well.id] = on
            self._deliverability(well, q, on)

    def _deliverability(self, well: Well, q: Expr, on: Expr) -> None:
        ws = self.state.wells[well.id]
        curve = ws.curve.scaled(1 - deliverability_backoff(ws.sigma_rel, self.cfg))
        manifold = self.net.platform_by_id[well.platform].manifold
        u_lo = self.net.node_by_id[self.net.compressor.suction_node].p_min_bara ** 2
        lines = chord_lines(curve.rate_u, u_lo, curve.u_ws, tol=self.cfg.well_pwl_tol)
        relax = (1 - on) * well.rate_cap_mmscfd if not self._is_fixed() else 0.0
        for j, line in enumerate(lines):
            self.m.le(
                f"deliver[{well.id},{j}]",
                q - self.pm.u[manifold] * line.slope,
                relax + line.intercept,
                kind="well_deliverability",
                shared=False,
                meta={"well": well.id},
            )

    def _platform_flow(self, platform_id: str) -> Expr:
        return Expr.total(self.pm.rate[w.id] for w in self.net.wells_on(platform_id))

    def _routing(self) -> None:
        for platform in self.net.platforms:
            flow = self._platform_flow(platform.id)
            options = platform.export_options
            if len(options) == 1 or self._is_fixed():
                chosen = self.fixed.routes[platform.id] if self.fixed else options[0]
                for pipe_id in options:
                    self.pm.arc_flow[pipe_id] = flow if pipe_id == chosen else Expr()
                    self.pm.route[(platform.id, pipe_id)] = Expr({}, float(pipe_id == chosen))
                continue
            choices = {pipe_id: self.m.binary(f"r[{platform.id},{pipe_id}]") for pipe_id in options}
            self.m.eq(
                f"route_one[{platform.id}]",
                Expr.total(choices.values()),
                1.0,
                kind="route_choice",
                shared=False,
                meta={"platform": platform.id},
            )
            arcs = {}
            for pipe_id, choice in choices.items():
                cap = self.net.pipe_by_id[pipe_id].capacity_mmscfd
                arcs[pipe_id] = self.m.var(f"f[{pipe_id}]", 0.0, cap)
                self.m.le(f"route_link[{pipe_id}]", arcs[pipe_id], choice * cap, kind="route_choice", shared=False)
                self.pm.route[(platform.id, pipe_id)] = choice
            self.m.eq(
                f"route_balance[{platform.id}]",
                Expr.total(arcs.values()),
                flow,
                kind="route_choice",
                shared=False,
                meta={"platform": platform.id},
            )
            self.pm.arc_flow.update(arcs)

    def _compressor(self) -> None:
        comp = self.net.compressor
        suction, discharge = self.pm.u[comp.suction_node], self.pm.u[comp.discharge_node]
        self.pm.suction_u, self.pm.discharge_u = suction, discharge
        inflow = Expr.total(
            [
                *(
                    flow
                    for pid, flow in self.pm.arc_flow.items()
                    if self.net.pipe_by_id[pid].to_node == comp.suction_node
                ),
                *(self.state.third_party_rates[s.id] for s in self.net.third_party if s.node == comp.suction_node),
            ]
        )
        self.pm.hub_inflow = inflow
        ps_node = self.net.node_by_id[comp.suction_node]
        ambient = self.state.ambient_c
        self.pm.planes = conservative_planes(
            lambda ps, pd: self._bo(self.train_map.capacity_mmscfd(ps, pd, ambient)),
            ps_range=(ps_node.p_min_bara, ps_node.p_max_bara),
            pd_range=(self.net.arrival_node.setpoint_bara, comp.pd_max_bara),
            ratio_max=self.train_map.ratio_max,
            bands=self.cfg.compressor_bands,
        )
        worst_ratio_power = self.train_map.power_kw(1.0, 1.0, self.train_map.ratio_max)
        fuel_per_mmscfd = self.fuel_model.fuel_mmscfd(worst_ratio_power) - self.fuel_model.fuel_mmscfd(0.0)
        idle_per_train = self.fuel_model.fuel_mmscfd(0.0)

        available = self.state.trains_available
        if self._is_fixed():
            counts = {self.fixed.trains: Expr({}, 1.0)}
        else:
            counts = {m: self.m.binary(f"z[{m}]") for m in range(available + 1)}
            self.m.eq("train_one", Expr.total(counts.values()), 1.0, kind="train_choice", shared=False)
        self.pm.train_choice = counts
        self.pm.trains = Expr.total(z * m for m, z in counts.items())
        self.pm.fuel = inflow * fuel_per_mmscfd + self.pm.trains * idle_per_train

        big = 4000.0
        u0 = ((ps_node.p_min_bara + ps_node.p_max_bara) / 2) ** 2
        surge_per_bar = self.train_map.surge_min_mmscfd(ps_node.p_max_bara) / ps_node.p_max_bara
        for m, z in counts.items():
            relax = (1 - z) * big if not self._is_fixed() else 0.0
            if m == 0:
                self.m.le("compressor_off", inflow, relax, kind="compressor_capacity")
                continue
            for j, plane in enumerate(self.pm.planes):
                self.m.le(
                    f"compressor_cap[{m},{j}]",
                    inflow - (suction * plane.b + discharge * plane.c) * m,
                    relax + plane.a * m,
                    kind="compressor_capacity",
                )
            surge_line = math.sqrt(u0) + (suction - u0) * (1 / (2 * math.sqrt(u0)))
            self.m.ge(
                f"compressor_surge[{m}]", inflow + relax, surge_line * (surge_per_bar * m), kind="compressor_surge"
            )
        ratio_relax = counts[0] * comp.pd_max_bara**2 if 0 in counts and not self._is_fixed() else 0.0
        if self._is_fixed() and self.fixed.trains == 0:
            ratio_relax = comp.pd_max_bara**2
        self.m.le(
            "compressor_ratio", discharge - suction * self.train_map.ratio_max**2, ratio_relax, kind="compressor_ratio"
        )
        self.m.le("pd_max", discharge, comp.pd_max_bara**2, kind="pd_max")

    def _trunks(self) -> None:
        comp = self.net.compressor
        bypass = Expr.total(
            flow for pid, flow in self.pm.arc_flow.items() if self.net.pipe_by_id[pid].to_node == comp.discharge_node
        )
        self.pm.trunk_total = self.pm.hub_inflow - self.pm.fuel + bypass
        trunks = self.net.trunk_pipes
        weights = {p.id: 1 / math.sqrt(self.state.pipe_hydraulics[p.id].k_friction) for p in trunks}
        total_weight = sum(weights.values())
        for pipe in trunks:
            self.pm.arc_flow[pipe.id] = self.pm.trunk_total * (weights[pipe.id] / total_weight)

    def _pipes(self) -> None:
        for pipe in self.net.pipes:
            flow = self.pm.arc_flow.get(pipe.id, Expr())
            if not flow.terms:
                continue
            self._pipe_rows(pipe, flow)

    def _pipe_rows(self, pipe: Pipe, flow: Expr) -> None:
        hyd = self.state.pipe_hydraulics[pipe.id]
        lines = chord_lines(hyd.dp2, 0.0, pipe.capacity_mmscfd, tol=self.cfg.pipe_pwl_tol)
        platform = next((p.id for p in self.net.platforms if pipe.id in p.export_options), None)
        route = self.pm.route.get((platform, pipe.id)) if platform else None
        relax = (
            (1 - route) * self.net.node_by_id[pipe.from_node].maop_bara ** 2
            if route is not None and route.terms
            else 0.0
        )
        upstream = self.pm.u[pipe.from_node]
        downstream = self.pm.u[pipe.to_node] * hyd.outlet_factor
        for j, line in enumerate(lines):
            self.m.ge(
                f"pipe_dp[{pipe.id},{j}]",
                upstream - downstream + relax,
                flow * line.slope + line.intercept,
                kind="pipe_dp",
                meta={"pipe": pipe.id},
            )
        self.m.le(
            f"pipe_cap[{pipe.id}]", flow, self._bo(pipe.capacity_mmscfd), kind="pipe_capacity", meta={"pipe": pipe.id}
        )

    def _plant(self) -> None:
        plant, spec = self.net.plant, self.net.plant.spec
        kept = 1 - plant.shrink_frac
        inlet = self.pm.trunk_total
        ghv_ref = self._reference_ghv()
        co2_ref = self._reference_mix("co2")
        # Fuel is drawn from the commingled hub stream, so it takes CO2 with it. Using today's
        # mix keeps this linear; the verifier re-checks with the exact blend.
        co2_in = Expr.total(
            [
                *(self.pm.rate[w.id] * w.co2 for w in self.net.wells),
                *(self.state.third_party_rates[s.id] * s.co2 for s in self.net.third_party),
                self.pm.fuel * -co2_ref,
            ]
        )
        energy_raw = Expr.total(
            [
                *(self.pm.rate[w.id] * w.composition.ghv_btu_scf for w in self.net.wells),
                *(self.state.third_party_rates[s.id] * s.composition.ghv_btu_scf for s in self.net.third_party),
            ]
        )
        self.pm.co2_removed = self.m.var("co2_removed", 0.0, INF)
        removed = self.pm.co2_removed
        self.pm.sales_volume = inlet * kept - removed
        self.pm.sales_energy_total = (energy_raw - self.pm.fuel * ghv_ref) * kept
        volume = self.pm.sales_volume
        self.m.le("plant_inlet", inlet, self._bo(self.state.plant_inlet_cap_mmscfd), kind="plant_inlet")
        self.m.le("co2_removal", removed, self._bo(self.state.co2_removal_cap_mmscfd), kind="co2_removal")
        self.m.le("sales_co2", co2_in * kept - removed, volume * spec.co2_max, kind="sales_co2")
        self.m.ge("sales_ghv_min", self.pm.sales_energy_total, volume * spec.ghv_min_btu_scf, kind="sales_ghv")
        self.m.le("sales_ghv_max", self.pm.sales_energy_total, volume * spec.ghv_max_btu_scf, kind="sales_ghv")
        for label, attr, limit in (("h2s", "h2s_ppmv", spec.h2s_max_ppmv), ("hg", "hg_ug_sm3", spec.hg_max_ug_sm3)):
            excess = Expr.total(
                [
                    *(self.pm.rate[w.id] * (getattr(w, attr) - limit) for w in self.net.wells),
                    *(self.state.third_party_rates[s.id] * (getattr(s, attr) - limit) for s in self.net.third_party),
                ]
            )
            self.m.le(f"{label}_inlet", excess, 0.0, kind=f"{label}_inlet")

    def _reference_ghv(self) -> float:
        return self._reference_mix("ghv_btu_scf", default=1000.0)

    def _reference_mix(self, attr: str, *, default: float = 0.0) -> float:
        """Molar-average property of today's commingled stream at current rates."""
        flows = [(self.state.wells[w.id].rate_mmscfd, getattr(w.composition, attr)) for w in self.net.wells]
        flows += [(self.state.third_party_rates[s.id], getattr(s.composition, attr)) for s in self.net.third_party]
        total = sum(q for q, _ in flows)
        return sum(q * x for q, x in flows) / total if total > 0 else default

    def _our_share(self) -> float:
        ours = sum(ws.rate_mmscfd for ws in self.state.wells.values())
        theirs = sum(self.state.third_party_rates.values())
        return ours / (ours + theirs) if ours + theirs > 0 else 1.0

    def _liquids(self) -> None:
        wells, tp = self.net.wells, self.net.third_party
        rates = self.state.third_party_rates
        self.pm.condensate = Expr.total(
            [*(self.pm.rate[w.id] * w.cgr_bbl_mmscf for w in wells), *(rates[s.id] * s.cgr_bbl_mmscf for s in tp)]
        )
        self.pm.water = Expr.total(
            [*(self.pm.rate[w.id] * w.wgr_bbl_mmscf for w in wells), *(rates[s.id] * s.wgr_bbl_mmscf for s in tp)]
        )
        sc = self.net.slug_catcher
        self.m.le("slug_liquid", self.pm.condensate + self.pm.water, self._bo(sc.liquid_cap_bbld), kind="slug_liquid")
        self.m.le(
            "terminal_headroom", self.pm.condensate, self.state.condensate_headroom_bbld, kind="terminal_headroom"
        )
        for platform in self.net.platforms:
            water = Expr.total(self.pm.rate[w.id] * w.wgr_bbl_mmscf for w in self.net.wells_on(platform.id))
            self.m.le(
                f"platform_water[{platform.id}]",
                water,
                platform.water_cap_bbld,
                kind="platform_water",
                meta={"platform": platform.id},
            )
        self._meg()
        self._slug_sweep()

    def _meg(self) -> None:
        from neo.physics.hydrate import meg_injection_per_bbl_water, required_subcooling_c

        meg, comp = self.net.meg, self.net.compressor
        curve = max((p.hydrate.curve for p in self.net.platforms), key=lambda c: c.temperature_c(comp.pd_max_bara))
        subcooling = required_subcooling_c(
            curve, p_bara=comp.pd_max_bara, t_min_c=meg.seabed_temp_c, margin_c=meg.hydrate_margin_c
        )
        dose = meg_injection_per_bbl_water(subcooling, lean_meg_wt=meg.lean_wt)
        if dose > 0:
            self.m.le("meg", self.pm.water * dose, meg.cap_bbld, kind="meg")

    def _slug_sweep(self) -> None:
        sc = self.net.slug_catcher
        trunks = [p for p in self.net.trunk_pipes if p.holdup is not None]
        if not trunks:
            return
        total0 = self._current_trunk_total()
        weights = {p.id: 1 / math.sqrt(self.state.pipe_hydraulics[p.id].k_friction) for p in self.net.trunk_pipes}
        total_weight = sum(weights.values())
        inventory0, tangent_sum = 0.0, Expr()
        for pipe in trunks:
            q0 = total0 * weights[pipe.id] / total_weight
            inventory0 += pipe.holdup.inventory_bbl(q0)
            slope, intercept = pipe.holdup.tangent(q0)
            tangent_sum = tangent_sum + self.pm.arc_flow[pipe.id] * slope + intercept
        self.m.ge("slug_sweep", tangent_sum, inventory0 - sc.sweep_fraction * sc.free_volume_bbl, kind="slug_sweep")

    def _current_trunk_total(self) -> float:
        wells = sum(ws.rate_mmscfd for ws in self.state.wells.values())
        return wells + sum(self.state.third_party_rates.values())

    def _contracts(self) -> None:
        commercial = self.reg.commercial
        weighted = []
        for contract in commercial.gsas:
            nominated = self.state.nominations_mmbtu_d.get(contract.id, 0.0)
            upper = min(nominated, contract.mdq_mmbtu_d or nominated)
            d = self.m.var(f"d[{contract.id}]", 0.0, upper)
            self.pm.delivered[contract.id] = d
            self.pm.shortfall[contract.id] = nominated - d
            weighted.append((nominated - d) * priority_weight(contract.priority))
        for outlet in commercial.excess_outlets:
            self.pm.excess[outlet.id] = self.m.var(f"e[{outlet.id}]", 0.0, outlet.cap_mmbtu_d)
        self.pm.weighted_shortfall = Expr.total(weighted)
        our_energy = (
            Expr.total(self.pm.rate[w.id] * w.composition.ghv_btu_scf for w in self.net.wells)
            - self.pm.fuel * (self._reference_ghv() * self._our_share())
        ) * (1 - self.net.plant.shrink_frac)
        allocated = Expr.total([*self.pm.delivered.values(), *self.pm.excess.values()])
        self.m.eq("energy_balance", allocated - our_energy, 0.0, kind="energy_balance")
        if self.caps.weighted_shortfall is not None and self.stage == "value":
            self.m.le(
                "commitment",
                self.pm.weighted_shortfall,
                self.caps.weighted_shortfall + self.cfg.shortfall_eps,
                kind="commitment",
            )

    def _moves(self) -> None:
        sigmas, moves = [], []
        for well in self.net.wells:
            ws = self.state.wells[well.id]
            q = self.pm.rate[well.id]
            if not ws.available or not q.terms:
                continue
            up = self.m.var(f"dq_up[{well.id}]")
            down = self.m.var(f"dq_down[{well.id}]")
            slack = self.m.var(f"sigma[{well.id}]")
            limit = max(self.cfg.move_abs_mmscfd, self.cfg.move_rel * ws.rate_mmscfd)
            tags = {"kind": "move", "shared": False, "meta": {"well": well.id}}
            self.m.eq(f"move_def[{well.id}]", q - up + down, ws.rate_mmscfd, **tags)
            self.m.le(f"move_limit[{well.id}]", up + down - slack, limit, **tags)
            sigmas.append(slack)
            moves.append(up + down)
        self.pm.sigma_total = Expr.total(sigmas)
        self.pm.moves = Expr.total(moves)
        if self.caps.sigma is not None and self.stage in ("deliver", "value"):
            self.m.le("restore_cap", self.pm.sigma_total, self.caps.sigma + self.cfg.restore_eps, kind="restore_cap")

    def _objective(self) -> None:
        reg, net = self.reg, self.net
        gsas = reg.commercial.gsas
        reference_price = sum(c.price_usd_mmbtu * c.dcq_mmbtu_d for c in gsas) / sum(c.dcq_mmbtu_d for c in gsas)
        kept = 1 - net.plant.shrink_frac
        parts = {
            "gas_sales": Expr.total(
                [
                    *(self.pm.delivered[c.id] * c.price_usd_mmbtu for c in gsas),
                    *(self.pm.excess[o.id] * o.price_usd_mmbtu for o in reg.commercial.excess_outlets),
                ]
            ),
            "partner_share": Expr.total(
                self.pm.rate[w.id]
                * (-(1 - reg.psc.by_id[w.psc].netback_factor) * reference_price * kept * w.composition.ghv_btu_scf)
                for w in net.wells
            ),
            "condensate": Expr.total(
                self.pm.rate[w.id]
                * (reg.commercial.condensate_usd_bbl * reg.psc.by_id[w.psc].netback_factor * w.cgr_bbl_mmscf)
                for w in net.wells
            ),
            "opex": Expr.total(
                self.pm.rate[w.id] * -net.platform_by_id[w.platform].opex_usd_per_mmscf for w in net.wells
            ),
        }
        if self.stage == "rank":
            parts["shortfall_penalty"] = Expr.total(self.pm.shortfall[c.id] * -c.shortfall_usd_mmbtu for c in gsas)
        self.pm.value_parts = parts
        match self.stage:
            case "restore":
                self.m.objective = self.pm.sigma_total
            case "deliver":
                self.m.objective = self.pm.weighted_shortfall
            case "value":
                self.m.objective = self.pm.value - self.pm.moves * self.cfg.swing_usd_per_mmscfd
            case "rank":
                self.m.objective = self.pm.value
            case _:
                raise ValueError(f"unknown stage {self.stage!r}")


def build_plan_model(
    registry: Registry,
    state: NetworkState,
    *,
    stage: Stage,
    cfg: ModelConfig | None = None,
    fixed: DiscretePlan | None = None,
    caps: StageCaps | None = None,
) -> PlanModel:
    return _Builder(registry, state, cfg or ModelConfig(), stage, fixed, caps or StageCaps()).build()
