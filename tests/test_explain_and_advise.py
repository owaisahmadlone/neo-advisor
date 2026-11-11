from __future__ import annotations

import json

import pytest

from neo.advise import AdviseConfig, advise
from neo.calibrate.reconcile import Balance, reconcile
from neo.domain.advisory import Advisory
from neo.domain.state import DataFlag
from neo.explain.merit import decompose, merit_order
from neo.opt.solve import solve_gas_day
from neo.publish.writers import write_bundle

FAST = AdviseConfig(value_samples=8)


@pytest.fixture(scope="module")
def solved(registry, state):
    return solve_gas_day(registry, state)


def test_stationarity_identity_holds_for_every_well(solved):
    rank = solved.stages["rank"]
    for well_id, d in decompose(rank.pm, rank.solution).items():
        assert d.reduced_cost == pytest.approx(d.all_rows, abs=1e-6), well_id


def test_merit_order_is_sorted_and_explained(registry, state, solved):
    mo = merit_order(registry, state, solved.stages["rank"], solved.numbers.discrete)
    values = [m.unit_value for m in mo]
    assert values == sorted(values, reverse=True)
    assert [m.rank for m in mo] == list(range(1, len(mo) + 1))
    for m in mo:
        if m.status == "at_max":
            assert m.unit_value > 0 and m.reasons
    # condensate-rich, low-CO2 wells outrank lean, high-CO2 wells today
    rank = {m.well_id: m.rank for m in mo}
    assert min(rank[w] for w in rank if w.startswith("P2")) < min(rank[w] for w in rank if w.startswith("P1"))


def test_unit_value_matches_finite_difference_for_a_capacity_bound_well(registry, state, solved):
    mo = merit_order(registry, state, solved.stages["rank"], solved.numbers.discrete, contributions=False)
    top = next(m for m in mo if m.status == "at_max")
    rank = solved.stages["rank"]
    base = rank.solution.objective
    bumped_state = state.with_overrides(wells={**state.wells, top.well_id: _scaled(state.wells[top.well_id], 1.01)})
    from neo.opt.solve import solve_rank

    bumped = solve_rank(registry, bumped_state, solved.numbers.discrete)
    extra = bumped.solution.value(bumped.pm.rate[top.well_id]) - rank.solution.value(rank.pm.rate[top.well_id])
    assert extra > 0
    assert (bumped.solution.objective - base) / extra == pytest.approx(top.unit_value, rel=0.15)


def _scaled(ws, factor):
    from dataclasses import replace

    return replace(ws, curve=ws.curve.scaled(factor), rate_mmscfd=ws.rate_mmscfd)


def test_advisory_is_byte_identical_on_rerun(registry, state, tmp_path):
    first = write_bundle(advise(registry, state, cfg=FAST), tmp_path / "a")
    second = write_bundle(advise(registry, state, cfg=FAST), tmp_path / "b")
    assert (first / "advisory.json").read_bytes() == (second / "advisory.json").read_bytes()
    Advisory.model_validate(json.loads((first / "advisory.json").read_text()))
    assert (first / "advisory_report.md").read_text().startswith("# NeO advisory")


def test_every_well_has_a_rank_and_a_reason(registry, state):
    advisory = advise(registry, state, cfg=FAST).advisory
    assert advisory.status in ("ready", "ready_with_flags")
    for w in advisory.wells:
        assert w.rank is not None and w.reasons, w.well_id


def test_critical_data_blocks_the_run(registry, state):
    bad = state.with_overrides(flags=(DataFlag("critical_tag_bad", "HUBS.PRESS", "no Good samples", "critical"),))
    advisory = advise(registry, bad, cfg=FAST).advisory
    assert advisory.status == "blocked"
    assert advisory.status_reasons[0].code == "critical_tag_bad"
    assert advisory.wells == []


def test_client_advisory_carries_no_internals(registry, state):
    text = advise(registry, state, cfg=FAST).advisory.model_dump_json()
    for internal in ("backoff", "swing_usd", "deliverability_k", "HiGHS", "sigma", "pwl", "restore_eps"):
        assert internal not in text


def test_reconciliation_meets_the_meter_exactly():
    out = reconcile(
        {"a": 10.0, "b": 20.0, "c": 5.0}, {"a": 1.0, "b": 2.0, "c": 0.5}, [Balance("hub", ["a", "b"], 33.0)]
    )
    assert out["a"] + out["b"] == pytest.approx(33.0)
    assert out["c"] == 5.0
    assert (out["b"] - 20.0) == pytest.approx(4 * (out["a"] - 10.0))  # adjustment proportional to variance
