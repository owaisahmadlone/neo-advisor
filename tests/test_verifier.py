from __future__ import annotations

import pytest

from neo.advise import advisor_physics, plan_setpoints
from neo.opt.solve import solve_gas_day
from neo.verify.forward import Setpoints, simulate


@pytest.fixture(scope="module")
def plan(registry, state):
    return solve_gas_day(registry, state)


def test_optimiser_pressures_bound_the_physical_ones(registry, state, plan):
    """Conservative chords: the model's manifold and discharge pressures are upper bounds."""
    stage = plan.stages["value_lp"]
    physical = simulate(advisor_physics(registry, state), plan_setpoints(plan.numbers)).pressures
    for node in ("P1M", "P2M", "P3M", "HUBD"):
        model_p = stage.solution.value(stage.pm.u[node]) ** 0.5
        assert model_p >= physical[node] - 1e-6, node


def test_overloaded_plan_is_caught(registry, state, plan):
    sp = plan_setpoints(plan.numbers)
    overloaded = Setpoints({w: q * 1.6 for w, q in sp.rates.items()}, sp.routes, sp.trains, sp.suction_bara)
    limits = {v.limit for v in simulate(advisor_physics(registry, state), overloaded).hard_violations}
    assert {"well_deliverability", "co2_removal"} <= limits


def test_lost_train_is_caught(registry, state, plan):
    sp = plan_setpoints(plan.numbers)
    one_train = Setpoints(sp.rates, sp.routes, 1, sp.suction_bara)
    limits = {v.limit for v in simulate(advisor_physics(registry, state), one_train).hard_violations}
    assert "compressor_capacity" in limits


def test_respond_mode_clips_to_deliverability_and_raises_suction(registry, state, plan):
    sp = plan_setpoints(plan.numbers)
    greedy = Setpoints({w: 80.0 for w in sp.rates}, sp.routes, 2, sp.suction_bara)
    outcome = simulate(advisor_physics(registry, state), greedy, mode="respond")
    assert all(o.rate < 80.0 for o in outcome.wells.values())
    assert outcome.pressures["HUBS"] > sp.suction_bara
