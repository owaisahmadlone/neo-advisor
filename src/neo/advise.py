"""The advisory pipeline from a validated ``NetworkState`` to a publishable advisory.

gate -> lexicographic solve -> nonlinear verification (tighten and retry, else block)
     -> baseline comparison -> merit order -> value band -> advisory + manifest + report
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime

import numpy as np

import neo
from neo.core.ids import stable_hash
from neo.domain.advisory import (
    Advisory,
    BindingConstraint,
    ContractAllocation,
    ExcessAllocation,
    NetworkAdvice,
    SalesGas,
    StatusReason,
    ValueLine,
    ValueRange,
    WellAdvice,
    WellReason,
)
from neo.domain.registry import Registry
from neo.domain.state import NetworkState
from neo.explain.labels import describe_violation, limit_label, row_kind_label
from neo.explain.merit import MeritOrder, merit_order
from neo.opt.build import ModelConfig
from neo.opt.solve import PlanNumbers, PlanSolution, solve_gas_day
from neo.verify.forward import Outcome, PhysicalNetwork, Setpoints, WellPhysics, simulate
from neo.verify.value import Valuation, value_outcome

MAINTAIN_TOL_MMSCFD = 0.5


@dataclass(frozen=True, slots=True)
class AdviseConfig:
    model: ModelConfig = field(default_factory=ModelConfig)
    verify_retries: int = 2
    value_samples: int = 60
    contributions: bool = True


@dataclass(frozen=True, slots=True)
class AdviceBundle:
    advisory: Advisory
    manifest: dict
    nomination_draft: list[dict]
    merit: MeritOrder | None = None
    plan: PlanNumbers | None = None
    plan_outcome: Outcome | None = None
    baseline_outcome: Outcome | None = None
    solution: PlanSolution | None = None


def advisor_physics(
    registry: Registry, state: NetworkState, *, curve_scale: Mapping[str, float] | None = None
) -> PhysicalNetwork:
    net = registry.network
    scale = curve_scale or {}
    wells = {
        w.id: WellPhysics(
            model=state.wells[w.id].curve.scaled(scale.get(w.id, 1.0)),
            platform=w.platform,
            composition=w.composition,
            cgr=w.cgr_bbl_mmscf,
            wgr=w.wgr_bbl_mmscf,
            wellhead_temp_c=w.wellhead_temp_c,
            choke_bore_in=w.choke_bore_in,
        )
        for w in net.wells
    }
    return PhysicalNetwork(
        network=net,
        wells=wells,
        pipes=dict(state.pipe_hydraulics),
        holdups={p.id: p.holdup for p in net.pipes if p.holdup is not None},
        train=net.compressor.train.map,
        fuel=net.compressor.fuel.model,
        ambient_c=state.ambient_c,
        third_party={
            s.id: (state.third_party_rates[s.id], s.composition, s.cgr_bbl_mmscf, s.wgr_bbl_mmscf)
            for s in net.third_party
        },
        plant_inlet_cap=state.plant_inlet_cap_mmscfd,
        co2_removal_cap=state.co2_removal_cap_mmscfd,
        condensate_headroom=state.condensate_headroom_bbld,
    )


def move_limits(state: NetworkState, cfg: ModelConfig) -> dict[str, float]:
    return {w: max(cfg.move_abs_mmscfd, cfg.move_rel * s.rate_mmscfd) for w, s in state.wells.items()}


def current_setpoints(state: NetworkState) -> Setpoints:
    rates = {w: s.rate_mmscfd if s.online else 0.0 for w, s in state.wells.items()}
    trains = min(state.current.trains_running, state.trains_available)
    return Setpoints(rates, dict(state.current.routes), trains, state.current.suction_bara)


def plan_setpoints(plan: PlanNumbers) -> Setpoints:
    return Setpoints(dict(plan.rates), dict(plan.routes), plan.trains, plan.suction_bara)


def run_id_for(registry: Registry, state: NetworkState, cfg: AdviseConfig, input_hashes: Mapping[str, str]) -> str:
    return stable_hash(
        {
            "gas_day": state.gas_day,
            "cutoff": state.snapshot_cutoff_utc,
            "revision": state.revision,
            "inputs": dict(sorted(input_hashes.items())),
            "registry": registry.network.version,
            "code": neo.__version__,
            "config": asdict(cfg),
        }
    )


def _action(current: float, recommended: float, available: bool) -> str:
    if not available:
        return "unavailable"
    if current <= 0 and recommended > 0:
        return "open"
    if current > 0 and recommended <= 0:
        return "shut_in"
    if abs(recommended - current) < MAINTAIN_TOL_MMSCFD:
        return "maintain"
    return "increase" if recommended > current else "decrease"


def _solve_verified(
    registry: Registry, state: NetworkState, cfg: AdviseConfig, physics: PhysicalNetwork
) -> tuple[PlanSolution, Outcome | None, list[dict]]:
    attempts = []
    model_cfg = cfg.model
    limits = move_limits(state, cfg.model)
    current = {w: s.rate_mmscfd for w, s in state.wells.items()}
    for attempt in range(cfg.verify_retries + 1):
        solution = solve_gas_day(registry, state, model_cfg)
        if not solution.ok:
            attempts.append({"attempt": attempt, "solve": solution.status})
            return solution, None, attempts
        outcome = simulate(physics, plan_setpoints(solution.numbers), current_rates=current, move_limits=limits)
        attempts.append(
            {
                "attempt": attempt,
                "solve": solution.status,
                "seconds": round(solution.total_seconds, 4),
                "hard_violations": [str(v) for v in outcome.hard_violations],
            }
        )
        if not outcome.hard_violations:
            return solution, outcome, attempts
        model_cfg = replace(
            model_cfg,
            capacity_backoff=model_cfg.capacity_backoff + 0.02,
            deliverability_k=model_cfg.deliverability_k + 0.5,
        )
    return solution, outcome, attempts


def _value_band(
    registry: Registry, state: NetworkState, plan: PlanNumbers, run_id: str, samples: int
) -> ValueRange | None:
    if samples <= 0:
        return None
    rng = np.random.default_rng(int(run_id[:8], 16))
    values = []
    for _ in range(samples):
        scale = {w: float(np.clip(rng.normal(1.0, s.sigma_rel), 0.6, 1.4)) for w, s in state.wells.items()}
        physics = advisor_physics(registry, state, curve_scale=scale)
        outcome = simulate(physics, plan_setpoints(plan), mode="respond")
        values.append(value_outcome(registry, outcome, state.nominations_mmbtu_d).value)
    p10, p50, p90 = np.percentile(values, [10, 50, 90])
    return ValueRange(p10_usd_d=round(p10, 0), p50_usd_d=round(p50, 0), p90_usd_d=round(p90, 0))


def blocked_advisory(
    registry: Registry, state: NetworkState, run_id: str, reasons: list[StatusReason], *, supersedes: str | None = None
) -> Advisory:
    start, end = registry.network.calendar.bounds_utc(state.gas_day)
    return Advisory(
        run_id=run_id,
        revision=state.revision,
        supersedes=supersedes,
        gas_day=state.gas_day,
        valid_from_utc=start,
        valid_to_utc=end,
        snapshot_cutoff_utc=state.snapshot_cutoff_utc,
        status="blocked",
        status_reasons=reasons,
        wells=[],
        network=None,
        sales_gas=None,
        allocations=[],
        excess=[],
        value_waterfall=[],
        plan_value_usd_d=None,
        marginal_gas_value_usd_mmbtu=None,
        binding_constraints=[],
        data_flags=_data_flags(state),
    )


def _data_flags(state: NetworkState) -> list[StatusReason]:
    return [StatusReason(code=f.code, message=f.message, subject=f.subject) for f in state.flags]


def advise(
    registry: Registry,
    state: NetworkState,
    *,
    cfg: AdviseConfig | None = None,
    input_hashes: Mapping[str, str] | None = None,
    supersedes: str | None = None,
) -> AdviceBundle:
    cfg = cfg or AdviseConfig()
    hashes = dict(input_hashes or {})
    run_id = run_id_for(registry, state, cfg, hashes)
    manifest: dict = {
        "run_id": run_id,
        "code_version": neo.__version__,
        "registry_version": registry.network.version,
        "gas_day": state.gas_day.isoformat(),
        "revision": state.revision,
        "snapshot_cutoff_utc": state.snapshot_cutoff_utc.isoformat(),
        "input_hashes": hashes,
        "model_config": asdict(cfg.model),
    }
    if state.blocking_flags:
        reasons = [StatusReason(code=f.code, message=f.message, subject=f.subject) for f in state.blocking_flags]
        manifest["status"] = "blocked: critical data"
        return AdviceBundle(blocked_advisory(registry, state, run_id, reasons, supersedes=supersedes), manifest, [])

    started = time.perf_counter()
    physics = advisor_physics(registry, state)
    solution, outcome, attempts = _solve_verified(registry, state, cfg, physics)
    manifest["solve_attempts"] = attempts
    if not solution.ok:
        rows = solution.infeasibility.rows if solution.infeasibility else ()
        kinds = sorted({kind for _, kind, _ in rows})
        reasons = [
            StatusReason(
                code="infeasible",
                message=f"{row_kind_label(kind)} cannot be met together with the other limits on today's data",
            )
            for kind in kinds
        ]
        reasons = reasons or [StatusReason(code="solve_failed", message="no plan could be computed from today's data")]
        manifest["status"] = f"blocked: {solution.status}"
        return AdviceBundle(blocked_advisory(registry, state, run_id, reasons, supersedes=supersedes), manifest, [])
    if outcome.hard_violations:
        reasons = [
            StatusReason(code="verification_failed", message=describe_violation(v), subject=v.subject)
            for v in outcome.hard_violations
        ]
        manifest["status"] = "blocked: verification failed"
        return AdviceBundle(blocked_advisory(registry, state, run_id, reasons, supersedes=supersedes), manifest, [])

    plan = solution.numbers
    # What holding today's settings would actually do: wells clipped, suction floating.
    baseline_outcome = simulate(physics, current_setpoints(state), mode="respond")
    plan_value = value_outcome(registry, outcome, state.nominations_mmbtu_d)
    base_value = value_outcome(registry, baseline_outcome, state.nominations_mmbtu_d)
    merit = merit_order(
        registry, state, solution.stages["rank"], plan.discrete, cfg.model, contributions=cfg.contributions
    )
    band = _value_band(registry, state, plan, run_id, cfg.value_samples)
    manifest["seconds_total"] = round(time.perf_counter() - started, 3)
    manifest["stage_seconds"] = {k: round(s.seconds, 4) for k, s in solution.stages.items()}
    manifest["stage_status"] = {k: s.solution.status for k, s in solution.stages.items()}
    manifest["baseline_hard_violations"] = [str(v) for v in baseline_outcome.hard_violations]
    manifest["verifier_warnings"] = [str(v) for v in outcome.violations if v.severity == "warning"]
    manifest["elastic_move_slack_mmscfd"] = round(plan.sigma_total, 4)

    advisory = _assemble(
        registry,
        state,
        run_id,
        supersedes,
        plan,
        outcome,
        baseline_outcome,
        plan_value,
        base_value,
        merit,
        band,
        solution,
    )
    draft = [
        {
            "gas_day": state.gas_day.isoformat(),
            "contract_id": a.contract_id,
            "nominated_mmbtu_d": round(a.nominated_mmbtu_d, 0),
            "deliverable_mmbtu_d": round(a.planned_mmbtu_d, 0),
            "note": "draft for planner review; not submitted",
        }
        for a in advisory.allocations
    ]
    manifest["status"] = advisory.status
    return AdviceBundle(advisory, manifest, draft, merit, plan, outcome, baseline_outcome, solution)


def _assemble(
    registry: Registry,
    state: NetworkState,
    run_id: str,
    supersedes: str | None,
    plan: PlanNumbers,
    outcome: Outcome,
    baseline: Outcome,
    plan_value: Valuation,
    base_value: Valuation,
    merit: MeritOrder,
    band: ValueRange | None,
    solution: PlanSolution,
) -> Advisory:
    net = registry.network
    rank_stage = solution.stages["rank"]
    destination = {w: rank_stage.solution.value(q) for w, q in rank_stage.pm.rate.items()}
    limits = move_limits(state, ModelConfig())
    by_well = {m.well_id: m for m in merit}
    wells = []
    for well in net.wells:
        ws = state.wells[well.id]
        current = ws.rate_mmscfd if ws.online else 0.0
        recommended = round(plan.rates[well.id], 3)
        m = by_well[well.id]
        wo = outcome.wells.get(well.id)
        flags = []
        if (
            destination.get(well.id, 0.0) - recommended > MAINTAIN_TOL_MMSCFD
            and abs(recommended - current) >= limits[well.id] - 1e-3
        ):
            flags.append("held_by_step_limit")
        if ws.confidence == "low":
            flags.append("low_deliverability_confidence")
        if any(v.subject == well.id and v.limit == "choke_hydrate_margin" for v in outcome.violations):
            flags.append("choke_hydrate_margin")
        opening = wo.choke_opening_pct if wo and wo.rate > 0 else None
        if m.status == "at_max" and recommended > 0:
            # Limited only by its own deliverability: the network wants all it can give, so the
            # instruction is "open fully"; the rate shown is the expected rate, not a cap.
            flags.append("open_fully")
        elif opening is not None and opening >= 100:
            flags.append("choke_fully_open")
        wells.append(
            WellAdvice(
                item_id=f"well:{well.id}",
                well_id=well.id,
                platform=well.platform,
                action="open_fully"
                if "open_fully" in flags and current > 0
                else _action(current, recommended, ws.available),
                current_rate_mmscfd=round(current, 3),
                recommended_rate_mmscfd=recommended,
                implied_choke_pct=round(min(opening, 100.0), 1) if opening is not None else None,
                wellhead_pressure_bara=round(wo.wellhead_bara, 2) if wo and wo.rate > 0 else None,
                rank=m.rank if m.status != "unavailable" else None,
                merit_status=m.status,
                unit_value_usd_per_mmscfd=round(m.unit_value, 0) if m.status != "unavailable" else None,
                total_contribution_usd_d=round(m.total_contribution, 0) if m.total_contribution is not None else None,
                reasons=[
                    WellReason(key=r.key, label=r.label, usd_per_mmscfd=round(r.usd_per_mmscfd, 0))
                    for r in m.reasons[:6]
                ],
                confidence=ws.confidence,
                flags=flags,
            )
        )

    comp = net.compressor
    ps, pd = plan.suction_bara, outcome.pressures[comp.discharge_node]
    capacity = plan.trains * comp.train.map.capacity_mmscfd(ps, pd, state.ambient_c)
    network = NetworkAdvice(
        suction_setpoint_bara=round(ps, 2),
        trains_online=plan.trains,
        trains_available=state.trains_available,
        routes=dict(plan.routes),
        hub_inflow_mmscfd=round(outcome.hub_inflow, 2),
        compression_ullage_mmscfd=round(capacity - outcome.hub_inflow, 2),
        discharge_pressure_bara=round(pd, 2),
        arrival_pressure_bara=round(net.arrival_node.setpoint_bara, 2),
    )
    contracts = {c.id: c for c in registry.commercial.contracts}
    allocations = [
        ContractAllocation(
            contract_id=cid,
            buyer=contracts[cid].buyer,
            priority=contracts[cid].priority,
            nominated_mmbtu_d=round(state.nominations_mmbtu_d.get(cid, 0.0), 0),
            planned_mmbtu_d=round(plan_value.delivered[cid], 0),
            shortfall_mmbtu_d=round(plan_value.shortfall[cid], 0),
        )
        for cid in sorted(plan_value.delivered)
    ]
    excess = [
        ExcessAllocation(outlet_id=oid, buyer=contracts[oid].buyer, planned_mmbtu_d=round(q, 0))
        for oid, q in sorted(plan_value.excess.items())
    ]
    waterfall = [
        ValueLine(
            component=name,
            plan_usd_d=round(plan_value.parts[name], 0),
            baseline_usd_d=round(base_value.parts[name], 0),
            delta_usd_d=round(plan_value.parts[name] - base_value.parts[name], 0),
        )
        for name in plan_value.parts
    ]
    reasons = _status_reasons(state, plan, plan_value, outcome)
    held = sorted({v.limit for v in baseline.hard_violations})
    if held:
        reasons.append(
            StatusReason(
                code="hold_breaches_limits",
                message="holding current settings would breach: " + ", ".join(limit_label(h) for h in held),
            )
        )
    status = "ready_with_flags" if reasons or state.flags else "ready"
    start, end = net.calendar.bounds_utc(state.gas_day)
    return Advisory(
        run_id=run_id,
        revision=state.revision,
        supersedes=supersedes,
        gas_day=state.gas_day,
        valid_from_utc=start,
        valid_to_utc=end,
        snapshot_cutoff_utc=state.snapshot_cutoff_utc,
        status=status,
        status_reasons=reasons,
        wells=wells,
        network=network,
        sales_gas=SalesGas(
            volume_mmscfd=round(outcome.sales_volume, 2),
            ghv_btu_scf=round(outcome.sales_ghv, 1),
            co2_mol_pct=round(100 * outcome.sales_co2, 2),
            co2_removal_mmscfd=round(outcome.co2_removal_needed, 2),
        ),
        allocations=allocations,
        excess=excess,
        value_waterfall=waterfall,
        plan_value_usd_d=band,
        marginal_gas_value_usd_mmbtu=round(merit.marginal_gas_value_usd_mmbtu, 3),
        choke_back_order=choke_back_order(merit, plan),
        binding_constraints=[
            BindingConstraint(key=b.key, label=b.label, shadow_value=round(b.shadow_value, 1), unit=b.unit)
            for b in merit.binding
        ],
        data_flags=_data_flags(state),
    )


def choke_back_order(merit: MeritOrder, plan: PlanNumbers) -> list[str]:
    """Flowing wells from lowest to highest unit value: who gives way first when a limit tightens."""
    flowing = [m for m in merit if plan.rates.get(m.well_id, 0.0) > 0 and m.status != "unavailable"]
    return [m.well_id for m in sorted(flowing, key=lambda m: (m.unit_value, m.well_id))]


def _status_reasons(state: NetworkState, plan: PlanNumbers, value: Valuation, outcome: Outcome) -> list[StatusReason]:
    reasons = []
    if plan.sigma_total > 1e-3:
        reasons.append(
            StatusReason(
                code="step_limits_relaxed", message="current rates cannot be held within limits; larger moves needed"
            )
        )
    for cid, short in value.shortfall.items():
        if short > 1.0:
            reasons.append(
                StatusReason(code="shortfall", message=f"{short:,.0f} MMBtu/d below nomination", subject=cid)
            )
    if value.unsold_mmbtu > 1.0:
        reasons.append(StatusReason(code="unsold_gas", message=f"{value.unsold_mmbtu:,.0f} MMBtu/d has no outlet"))
    for v in outcome.violations:
        if v.severity == "warning" and v.limit != "move_limit":
            reasons.append(StatusReason(code=v.limit, message=describe_violation(v), subject=v.subject))
    return reasons


def utc_now() -> datetime:  # isolated so the advisory itself never depends on wall-clock time
    return datetime.now().astimezone()
