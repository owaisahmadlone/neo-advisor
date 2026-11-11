"""Well merit order from the ranking LP's duals.

For each well's rate column, LP stationarity gives
    reduced_cost_i = c_i - sum_r pi_r * A_ri          (over all rows)
The unit value we report keeps only the *shared* network rows:
    v_i = c_i - sum_{shared r} pi_r * A_ri
so it reads as "direct value minus what the well costs the network". Each shared row's
term is one reason (CO2 removal, compression, back-pressure in a line, ...).

* v_i > 0  the well is held by its own deliverability: worth v_i per extra MMscfd.
* v_i ~ 0  a swing well absorbing a binding network limit.
* v_i < 0  it costs the network more than it earns: choke or shut first.
Wells that are off are ranked by the discrete change in value from forcing them on,
because a local dual does not see the jump to the minimum stable rate.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from neo.domain.registry import Registry
from neo.domain.state import NetworkState
from neo.opt.build import DiscretePlan, ModelConfig, PlanModel
from neo.opt.lp import Row, Solution
from neo.opt.solve import StageResult, solve_rank

Status = Literal["at_max", "swing", "at_min", "off", "unavailable"]
SWING_TOL_USD = 25.0

REASON_LABELS = {
    "energy_balance": "gas sales at today's marginal gas value",
    "co2_removal": "CO2 removal capacity",
    "sales_co2": "sales-gas CO2 spec",
    "sales_ghv": "sales-gas heating-value spec",
    "h2s_inlet": "H2S limit at the plant inlet",
    "hg_inlet": "mercury limit at the plant inlet",
    "plant_inlet": "plant inlet capacity",
    "compressor_capacity": "hub compression capacity",
    "compressor_surge": "compressor minimum flow",
    "compressor_ratio": "compressor pressure ratio",
    "pd_max": "compressor discharge pressure limit",
    "pipe_capacity": "line capacity",
    "pipe_dp": "back-pressure",
    "slug_liquid": "slug-catcher liquid handling",
    "slug_sweep": "slug-catcher room for swept liquid",
    "terminal_headroom": "condensate terminal headroom",
    "platform_water": "platform water handling",
    "meg": "MEG injection capacity",
}
DIRECT_LABELS = {
    "partner_share": "partners' and government share of gas value",
    "condensate": "condensate (enterprise share)",
    "opex": "variable operating cost",
    "shortfall_penalty": "contract shortfall penalty",
}
UNITS = {
    "pipe_dp": "USD/d per bar",
    "pd_max": "USD/d per bar",
    "compressor_ratio": "USD/d per bar of discharge",
    "slug_liquid": "USD/d per bbl/d",
    "terminal_headroom": "USD/d per bbl/d",
    "platform_water": "USD/d per bbl/d",
    "meg": "USD/d per bbl/d",
    "slug_sweep": "USD/d per bbl",
    "sales_ghv": "USD/d per Btu/scf-MMscfd",
}


@dataclass(frozen=True, slots=True)
class Reason:
    key: str
    label: str
    usd_per_mmscfd: float


@dataclass(frozen=True, slots=True)
class WellMerit:
    well_id: str
    status: Status
    unit_value: float  # USD/d per MMscfd
    total_contribution: float | None  # USD/d, from a forced on/off re-solve
    reasons: tuple[Reason, ...]
    rank: int = 0


@dataclass(frozen=True, slots=True)
class BindingLimit:
    key: str
    label: str
    shadow_value: float
    unit: str


@dataclass(frozen=True, slots=True)
class MeritOrder:
    wells: tuple[WellMerit, ...]
    binding: tuple[BindingLimit, ...]
    marginal_gas_value_usd_mmbtu: float

    def __iter__(self):
        return iter(self.wells)

    def __len__(self) -> int:
        return len(self.wells)


@dataclass(frozen=True, slots=True)
class WellDecomposition:
    unit_value: float
    reduced_cost: float
    shared: dict[tuple[str, str], float]
    direct: dict[str, float]
    all_rows: float  # sum over every row of -pi*A, for the stationarity check


def _row_key(row: Row) -> tuple[str, str]:
    return row.kind, row.meta.get("pipe") or row.meta.get("platform") or ""


def _label(kind: str, subject: str) -> str:
    base = REASON_LABELS.get(kind, kind.replace("_", " "))
    if not subject:
        return base
    return f"{base} in {subject}" if kind in ("pipe_dp", "pipe_capacity") else f"{base} on {subject}"


def decompose(pm: PlanModel, solution: Solution) -> dict[str, WellDecomposition]:
    if solution.row_dual is None or solution.col_dual is None:
        raise ValueError("decomposition needs an LP solution with duals")
    by_column: dict[int, list[int]] = defaultdict(list)
    for r, row in enumerate(pm.model.rows):
        for c in row.terms:
            by_column[c].append(r)
    out = {}
    for well_id, expr in pm.rate.items():
        if not expr.terms:
            continue
        col = next(iter(expr.terms))
        cost = pm.model.objective.terms.get(col, 0.0)
        shared: dict[tuple[str, str], float] = defaultdict(float)
        all_rows = 0.0
        for r in by_column[col]:
            row = pm.model.rows[r]
            term = -solution.row_dual[r] * row.terms[col]
            all_rows += term
            if row.shared:
                shared[_row_key(row)] += term
        direct = {name: part.terms[col] for name, part in pm.value_parts.items() if part.terms.get(col)}
        out[well_id] = WellDecomposition(
            unit_value=cost + sum(shared.values()),
            reduced_cost=float(solution.col_dual[col]),
            shared=dict(shared),
            direct=direct,
            all_rows=cost + all_rows,
        )
    return out


def _toggle(
    registry: Registry,
    state: NetworkState,
    discrete: DiscretePlan,
    well_id: str,
    base: StageResult,
    cfg: ModelConfig | None,
) -> tuple[float, float] | None:
    """(change in value from switching the well's status, the well's rate when on)."""
    on = well_id in discrete.wells_on
    toggled = solve_rank(registry, state, discrete.with_well(well_id, on=not on), cfg)
    if not toggled.optimal:
        return None
    if on:
        return base.solution.objective - toggled.solution.objective, base.solution.value(base.pm.rate[well_id])
    return toggled.solution.objective - base.solution.objective, toggled.solution.value(toggled.pm.rate[well_id])


def merit_order(
    registry: Registry,
    state: NetworkState,
    rank: StageResult,
    discrete: DiscretePlan,
    cfg: ModelConfig | None = None,
    *,
    contributions: bool = True,
) -> MeritOrder:
    parts = decompose(rank.pm, rank.solution)
    merits = []
    for well in registry.network.wells:
        if not state.wells[well.id].available:
            merits.append(WellMerit(well.id, "unavailable", float("-inf"), None, ()))
            continue
        d = parts[well.id]
        on = well.id in discrete.wells_on
        toggle = _toggle(registry, state, discrete, well.id, rank, cfg) if contributions else None
        unit = d.unit_value
        total = toggle[0] if toggle else None
        if not on:
            status: Status = "off"
            if toggle and toggle[1] > 0:
                unit = toggle[0] / toggle[1]
        elif abs(unit) <= SWING_TOL_USD:
            status = "swing"
        elif unit > 0:
            status = "at_max"
        else:
            status = "at_min"
        reasons = [Reason(f"{k}:{s}" if s else k, _label(k, s), v) for (k, s), v in d.shared.items() if abs(v) > 1e-6]
        reasons += [Reason(k, DIRECT_LABELS.get(k, k), v) for k, v in d.direct.items()]
        reasons.sort(key=lambda r: -abs(r.usd_per_mmscfd))
        merits.append(WellMerit(well.id, status, unit, total, tuple(reasons)))

    ordered = sorted(merits, key=lambda m: (-m.unit_value, m.well_id))
    ranked = tuple(
        WellMerit(m.well_id, m.status, m.unit_value, m.total_contribution, m.reasons, rank=i)
        for i, m in enumerate(ordered, start=1)
    )
    return MeritOrder(
        ranked, binding_limits(registry, rank.pm, rank.solution), marginal_gas_value(rank.pm, rank.solution)
    )


def binding_limits(
    registry: Registry, pm: PlanModel, solution: Solution, *, tol: float = 1e-3
) -> tuple[BindingLimit, ...]:
    net = registry.network
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for r, row in enumerate(pm.model.rows):
        if row.shared and row.kind != "energy_balance" and abs(solution.row_dual[r]) > tol:
            totals[_row_key(row)] += abs(float(solution.row_dual[r]))
    limits = []
    for (kind, subject), dual in totals.items():
        if kind == "pipe_dp":
            node = net.pipe_by_id[subject].from_node
            dual *= 2 * solution.value(pm.u[node]) ** 0.5  # d value/d P = d value/d P^2 * 2P
        elif kind in ("pd_max", "compressor_ratio"):
            dual *= 2 * solution.value(pm.discharge_u) ** 0.5
        key = f"{kind}:{subject}" if subject else kind
        limits.append(BindingLimit(key, _label(kind, subject), dual, UNITS.get(kind, "USD/d per MMscfd")))
    return tuple(sorted(limits, key=lambda b: -b.shadow_value))


def marginal_gas_value(pm: PlanModel, solution: Solution) -> float:
    """USD per MMBtu of one more unit of our sales energy (dual of the energy balance)."""
    index = next(i for i, row in enumerate(pm.model.rows) if row.kind == "energy_balance")
    return float(solution.row_dual[index])
