"""Lexicographic gas-day solve.

1. restore  (MILP) min elastic move-limit slack       -> sigma*
2. deliver  (MILP) min priority-weighted shortfall     -> W*      (sigma <= sigma*)
3. value    (MILP) max enterprise value near today     -> plan    (sigma, W capped)
4. value    (LP)   same, integers fixed                -> polished plan + duals
5. rank     (LP)   destination plan, integers fixed    -> merit-order duals

If stage 1 is infeasible, an elastic copy names the hard limits that cannot all hold.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from neo.domain.registry import Registry
from neo.domain.state import NetworkState
from neo.opt.build import DiscretePlan, ModelConfig, PlanModel, Stage, StageCaps, build_plan_model
from neo.opt.lp import Expr, LinearModel, Solution


@dataclass(frozen=True, slots=True)
class StageResult:
    stage: Stage
    pm: PlanModel
    solution: Solution
    seconds: float

    @property
    def optimal(self) -> bool:
        return self.solution.optimal


@dataclass(frozen=True, slots=True)
class Infeasibility:
    rows: tuple[tuple[str, str, float], ...]  # (row name, kind, violation)


@dataclass(frozen=True, slots=True)
class PlanNumbers:
    """Plain numbers read from a solved plan model."""

    rates: Mapping[str, float]
    on: Mapping[str, bool]
    routes: Mapping[str, str]
    trains: int
    suction_bara: float
    discharge_bound_bara: float
    arc_flow: Mapping[str, float]
    hub_inflow: float
    fuel: float
    co2_removed: float
    sales_volume: float
    sales_energy_total: float
    condensate: float
    water: float
    delivered: Mapping[str, float]
    excess: Mapping[str, float]
    shortfall: Mapping[str, float]
    value_parts: Mapping[str, float]
    sigma_total: float

    @property
    def value(self) -> float:
        return sum(self.value_parts.values())

    @property
    def discrete(self) -> DiscretePlan:
        return DiscretePlan(frozenset(w for w, on in self.on.items() if on), self.routes, self.trains)


@dataclass(frozen=True, slots=True)
class PlanSolution:
    status: str
    numbers: PlanNumbers | None
    stages: Mapping[str, StageResult] = field(default_factory=dict)
    infeasibility: Infeasibility | None = None

    @property
    def ok(self) -> bool:
        return self.status == "optimal"

    @property
    def total_seconds(self) -> float:
        return sum(s.seconds for s in self.stages.values())


def read_numbers(pm: PlanModel, sol: Solution) -> PlanNumbers:
    value = sol.value
    routes = {}
    for (platform, pipe), choice in pm.route.items():
        if value(choice) > 0.5:
            routes[platform] = pipe
    trains = next(m for m, z in pm.train_choice.items() if value(z) > 0.5)
    suction = value(pm.suction_u)
    discharge = value(pm.discharge_u)
    return PlanNumbers(
        rates={w: max(value(q), 0.0) for w, q in pm.rate.items()},
        on={w: value(y) > 0.5 for w, y in pm.on.items()},
        routes=routes,
        trains=trains,
        suction_bara=suction**0.5,
        discharge_bound_bara=discharge**0.5,
        arc_flow={p: value(f) for p, f in pm.arc_flow.items()},
        hub_inflow=value(pm.hub_inflow),
        fuel=value(pm.fuel),
        co2_removed=value(pm.co2_removed),
        sales_volume=value(pm.sales_volume),
        sales_energy_total=value(pm.sales_energy_total),
        condensate=value(pm.condensate),
        water=value(pm.water),
        delivered={c: value(d) for c, d in pm.delivered.items()},
        excess={k: value(e) for k, e in pm.excess.items()},
        shortfall={c: value(s) for c, s in pm.shortfall.items()},
        value_parts={k: value(v) for k, v in pm.value_parts.items()},
        sigma_total=value(pm.sigma_total),
    )


def _run(registry: Registry, state: NetworkState, cfg: ModelConfig, stage: Stage, **kwargs) -> StageResult:
    pm = build_plan_model(registry, state, stage=stage, cfg=cfg, **kwargs)
    started = time.perf_counter()
    solution = pm.model.solve()
    return StageResult(stage, pm, solution, time.perf_counter() - started)


def solve_gas_day(registry: Registry, state: NetworkState, cfg: ModelConfig | None = None) -> PlanSolution:
    cfg = cfg or ModelConfig()
    stages: dict[str, StageResult] = {}

    restore = stages["restore"] = _run(registry, state, cfg, "restore")
    if not restore.optimal:
        return PlanSolution("infeasible", None, stages, diagnose_infeasibility(restore.pm.model))
    sigma = restore.solution.objective

    deliver = stages["deliver"] = _run(registry, state, cfg, "deliver", caps=StageCaps(sigma=sigma))
    if not deliver.optimal:
        return PlanSolution(f"deliver stage {deliver.solution.status}", None, stages)
    caps = StageCaps(sigma=sigma, weighted_shortfall=deliver.solution.objective)

    value = stages["value"] = _run(registry, state, cfg, "value", caps=caps)
    if not value.optimal:
        return PlanSolution(f"value stage {value.solution.status}", None, stages)
    discrete = read_numbers(value.pm, value.solution).discrete

    polished = stages["value_lp"] = _run(registry, state, cfg, "value", caps=caps, fixed=discrete)
    if not polished.optimal:
        return PlanSolution(f"fixed-integer LP {polished.solution.status}", None, stages)
    stages["rank"] = _run(registry, state, cfg, "rank", fixed=discrete)
    return PlanSolution("optimal", read_numbers(polished.pm, polished.solution), stages)


def solve_rank(
    registry: Registry, state: NetworkState, discrete: DiscretePlan, cfg: ModelConfig | None = None
) -> StageResult:
    return _run(registry, state, cfg or ModelConfig(), "rank", fixed=discrete)


HARD_LIMIT_KINDS = frozenset(
    {
        "pipe_dp",
        "pipe_capacity",
        "compressor_capacity",
        "compressor_surge",
        "compressor_ratio",
        "pd_max",
        "plant_inlet",
        "co2_removal",
        "sales_co2",
        "sales_ghv",
        "h2s_inlet",
        "hg_inlet",
        "slug_liquid",
        "terminal_headroom",
        "platform_water",
        "meg",
        "slug_sweep",
        "energy_balance",
    }
)


def diagnose_infeasibility(model: LinearModel) -> Infeasibility:
    """Elastic copy: add slack to every hard-limit row, minimise total slack, report the
    rows that need it. Those are the limits (or the data behind them) that conflict."""
    elastic = LinearModel("min")
    elastic.columns = [type(c)(c.name, c.lower, c.upper, c.integer) for c in model.columns]
    slacks: list[tuple[str, str, Expr]] = []
    for row in model.rows:
        expr = Expr(dict(row.terms))
        if row.kind in HARD_LIMIT_KINDS:
            over = elastic.var(f"slack_up[{row.name}]")
            under = elastic.var(f"slack_down[{row.name}]")
            expr = expr - over + under
            slacks.append((row.name, row.kind, over + under))
        elastic.add(row.name, expr, lower=row.lower, upper=row.upper, kind=row.kind, shared=row.shared, meta=row.meta)
    elastic.objective = Expr.total(s for *_, s in slacks)
    solution = elastic.solve()
    violated = tuple((name, kind, round(solution.value(s), 6)) for name, kind, s in slacks if solution.value(s) > 1e-6)
    return Infeasibility(violated)
