"""The simulated control room.

It applies a policy's set-points to the truth plant, then protects the plant during the
day the way operators do: while a plant limit is breached, or gas has no buyer, choke the
next well in the policy's own give-way order. Every policy gets the same guard; only the
order differs (the advisor's merit order vs. the planners' highest-CO2-first rule).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date

from neo.domain.advisory import Advisory
from neo.domain.registry import Registry
from neo.opt.solve import PlanNumbers
from neo.synth import TruthPlant
from neo.synth.truth import DayResult
from neo.verify.forward import Setpoints

GUARDED_LIMITS = frozenset({"co2_removal", "sales_co2", "plant_inlet", "slug_liquid", "terminal_headroom"})
CHOKE_STEP = 0.9
MAX_STEPS = 60
UNSOLD_TOL_MMBTU = 50.0


def advisor_setpoints(registry: Registry, plan: PlanNumbers, advisory: Advisory) -> Setpoints:
    """Wells flagged "open fully" get their choke opened to the well's potential limit."""
    caps = {w.id: w.rate_cap_mmscfd for w in registry.network.wells}
    rates = dict(plan.rates)
    for advice in advisory.wells:
        if "open_fully" in advice.flags:
            rates[advice.well_id] = caps[advice.well_id]
    return Setpoints(rates, dict(plan.routes), plan.trains, plan.suction_bara)


def highest_co2_first(registry: Registry, setpoints: Setpoints) -> list[str]:
    co2 = {w.id: w.co2 for w in registry.network.wells}
    return sorted((w for w, q in setpoints.rates.items() if q > 0), key=lambda w: (-co2[w], w))


def _needs_guard(result: DayResult) -> bool:
    breached = any(v.limit in GUARDED_LIMITS for v in result.violations if v.severity == "hard")
    return breached or result.valuation.unsold_mmbtu > UNSOLD_TOL_MMBTU


def operate_guarded(
    plant: TruthPlant,
    gas_day: date,
    setpoints: Setpoints,
    order: Sequence[str],
    *,
    min_rates: Mapping[str, float],
    previous_rates: Mapping[str, float] | None,
) -> tuple[DayResult, int]:
    """Run the day; choke wells in ``order`` until guarded limits hold. Returns (result, steps)."""
    result = plant.operate(gas_day, setpoints, previous_rates=previous_rates)
    rates = dict(result.well_rates)
    queue = [w for w in order if rates.get(w, 0.0) > 0]
    steps = 0
    while _needs_guard(result) and queue and steps < MAX_STEPS:
        well = queue[0]
        reduced = rates[well] * CHOKE_STEP
        if reduced < min_rates[well]:
            reduced = 0.0
            queue.pop(0)
        rates[well] = reduced
        setpoints = replace(setpoints, rates={**setpoints.rates, **{w: rates[w] for w in rates}})
        result = plant.operate(gas_day, setpoints, previous_rates=previous_rates)
        steps += 1
    return result, steps
