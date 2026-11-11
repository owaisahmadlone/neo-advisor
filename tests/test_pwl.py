"""Every linearisation must err to the safe side on a dense grid."""

from __future__ import annotations

import numpy as np
import pytest

from neo.opt.pwl import chord_lines, conservative_planes


def _dense(lo: float, hi: float, n: int = 400) -> np.ndarray:
    return np.linspace(lo, hi, n)


def test_well_chords_never_exceed_the_curve(registry):
    u_lo = registry.network.node_by_id["HUBS"].p_min_bara ** 2
    for well in registry.network.wells:
        curve = well.curve
        lines = chord_lines(curve.rate_u, u_lo, curve.u_ws, tol=0.15)
        for u in _dense(u_lo, curve.u_ws):
            assert min(line(u) for line in lines) <= curve.rate_u(u) + 1e-9, well.id


def test_well_curves_are_concave_in_squared_pressure(registry):
    for well in registry.network.wells:
        u = _dense(registry.network.node_by_id["HUBS"].p_min_bara ** 2, well.curve.u_ws * 0.999)
        q = np.array([well.curve.rate_u(x) for x in u])
        assert np.all(np.diff(q, 2) <= 1e-9), well.id


def test_pipe_chords_never_understate_the_drop(registry):
    for pipe in registry.network.pipes:
        hyd = pipe.hydraulics
        lines = chord_lines(hyd.dp2, 0.0, pipe.capacity_mmscfd, tol=6.0)
        for q in _dense(0.0, pipe.capacity_mmscfd):
            assert max(line(q) for line in lines) >= hyd.dp2(q) - 1e-9, pipe.id


def test_compressor_planes_sit_under_the_map(registry):
    comp = registry.network.compressor
    train = comp.train.map
    ps_lo, ps_hi = registry.network.node_by_id["HUBS"].p_min_bara, registry.network.node_by_id["HUBS"].p_max_bara
    pd_lo = registry.network.arrival_node.setpoint_bara
    planes = conservative_planes(
        lambda ps, pd: train.capacity_mmscfd(ps, pd, 31.0),
        ps_range=(ps_lo, ps_hi),
        pd_range=(pd_lo, comp.pd_max_bara),
        ratio_max=train.ratio_max,
    )
    worst_gap = 0.0
    for ps in _dense(ps_lo, ps_hi, 70):
        for pd in _dense(pd_lo, comp.pd_max_bara, 70):
            if ps < pd <= train.ratio_max * ps:
                approx = min(p(ps**2, pd**2) for p in planes)
                truth = train.capacity_mmscfd(ps, pd, 31.0)
                assert approx <= truth + 1e-6
                worst_gap = max(worst_gap, truth - approx)
    assert worst_gap < 0.35 * train.capacity_mmscfd(ps_hi, pd_lo, 31.0)  # conservative, not useless


@pytest.mark.parametrize("tol", [0.05, 0.5])
def test_chord_tolerance_is_met(tol):
    lines = chord_lines(lambda x: np.sqrt(x), 1.0, 100.0, tol=tol)
    gaps = [np.sqrt(x) - min(line(x) for line in lines) for x in _dense(1.0, 100.0)]
    assert max(gaps) <= tol * 1.5
