from __future__ import annotations

import itertools
import time

import pytest

from neo.advise import advisor_physics, move_limits, plan_setpoints
from neo.domain.fixtures import FixtureSpec, state_from_registry
from neo.opt.build import DiscretePlan, ModelConfig, StageCaps, build_plan_model
from neo.opt.solve import read_numbers, solve_gas_day
from neo.verify.forward import simulate

SUBNET_WELLS = ("P1-W01", "P1-W03", "P2-W01", "P2-W02", "P2-W05")


@pytest.mark.parametrize("fixture", ["normal", "trip", "co2cut", "low_demand"])
def test_fixture_solves_fast_and_verifies(registry, fixture_state, fixture):
    state = fixture_state(fixture)
    started = time.perf_counter()
    result = solve_gas_day(registry, state)
    assert result.ok, result.status
    assert time.perf_counter() - started < 10
    outcome = simulate(
        advisor_physics(registry, state),
        plan_setpoints(result.numbers),
        current_rates={w: s.rate_mmscfd for w, s in state.wells.items()},
        move_limits=move_limits(state, ModelConfig()),
    )
    assert not outcome.hard_violations, [str(v) for v in outcome.hard_violations]


def test_trip_day_is_feasible_and_keeps_priority_buyer_whole(registry, fixture_state):
    numbers = solve_gas_day(registry, fixture_state("trip")).numbers
    assert numbers.trains <= 2
    assert numbers.shortfall["GSA-B1"] == pytest.approx(0.0, abs=1.0)


def test_co2_cut_chokes_high_co2_wells_first(registry, fixture_state):
    normal = solve_gas_day(registry, fixture_state("normal")).numbers
    cut = solve_gas_day(registry, fixture_state("co2cut")).numbers
    p1 = [w.id for w in registry.network.wells if w.platform == "P1"]
    assert sum(cut.rates[w] for w in p1) < sum(normal.rates[w] for w in p1)
    assert cut.co2_removed <= 12.0 * 0.98 + 1e-6


def _subnet(registry):
    net = registry.network.model_copy(update={"wells": [w for w in registry.network.wells if w.id in SUBNET_WELLS]})
    net.__dict__.pop("well_by_id", None)
    return registry.model_copy(update={"network": net})


def _lexicographic_key(registry, state, discrete):
    """Solve the three stages with integers fixed; return (sigma, shortfall, -value) or None."""
    cfg = ModelConfig()
    restore = build_plan_model(registry, state, stage="restore", cfg=cfg, fixed=discrete)
    r = restore.model.solve()
    if not r.optimal:
        return None
    deliver = build_plan_model(
        registry, state, stage="deliver", cfg=cfg, fixed=discrete, caps=StageCaps(sigma=r.objective)
    )
    d = deliver.model.solve()
    if not d.optimal:
        return None
    caps = StageCaps(sigma=r.objective, weighted_shortfall=d.objective)
    value = build_plan_model(registry, state, stage="value", cfg=cfg, fixed=discrete, caps=caps)
    v = value.model.solve()
    return (round(r.objective, 3), round(d.objective, 1), -v.objective) if v.optimal else None


def test_milp_matches_exhaustive_search_on_a_5_well_subnetwork(registry):
    sub = _subnet(registry)
    state = state_from_registry(sub, FixtureSpec(nomination_fraction={"GSA-B1": 0.35, "GSA-B2": 0.2}))
    milp = solve_gas_day(sub, state)
    assert milp.ok
    best = None
    for k in range(len(SUBNET_WELLS) + 1):
        for on in itertools.combinations(SUBNET_WELLS, k):
            for trains in range(state.trains_available + 1):
                discrete = DiscretePlan(
                    frozenset(on), {p.id: p.export_options[0] for p in sub.network.platforms}, trains
                )
                key = _lexicographic_key(sub, state, discrete)
                if key is not None and (best is None or key < best):
                    best = key
    milp_key = (
        round(milp.stages["restore"].solution.objective, 3),
        round(milp.stages["deliver"].solution.objective, 1),
        -milp.stages["value"].solution.objective,
    )
    assert best is not None
    assert milp_key[:2] == best[:2]
    assert milp_key[2] == pytest.approx(best[2], rel=1e-5)


def test_infeasible_limits_are_named(registry, state):
    broken = state.with_overrides(plant_inlet_cap_mmscfd=10.0)  # less than the third-party gas alone
    result = solve_gas_day(registry, broken)
    assert not result.ok
    assert any(kind == "plant_inlet" for _, kind, _ in result.infeasibility.rows)


def test_numbers_round_trip(registry, state):
    result = solve_gas_day(registry, state)
    stage = result.stages["value_lp"]
    again = read_numbers(stage.pm, stage.solution)
    assert again.value == pytest.approx(result.numbers.value)
