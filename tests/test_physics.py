from __future__ import annotations

import math
from datetime import UTC, date, datetime

import numpy as np
import pytest

from neo.core.gasday import GasDayCalendar
from neo.core.units import bara_to_barg, barg_to_bara
from neo.physics.blend import Composition, blend, co2_removal_needed, plant_outlet
from neo.physics.choke import opening_pct
from neo.physics.hydrate import meg_weight_pct
from neo.physics.pipes import PipeHydraulics, weymouth_coefficient
from neo.physics.wells import BottomholeWell, WellheadCurve, turner_min_rate


def test_unit_round_trip():
    assert bara_to_barg(barg_to_bara(60.0)) == pytest.approx(60.0)


def test_weymouth_matches_hand_calculation():
    # 24 in x 150 km wet-gas trunk at ~26 degC: P1 ~ 70.7 bara for 200 MMscfd into 60 bara
    k = weymouth_coefficient(length_km=150, diameter_in=24, efficiency=0.92, gas_sg=0.68, t_avg_c=26, z_avg=0.88)
    assert k == pytest.approx(0.03125, rel=1e-3)
    assert PipeHydraulics(k).inlet_pressure(60.0, 200.0) == pytest.approx(math.sqrt(3600 + k * 200**2))


def test_flanigan_term_makes_drop_convex_and_u_shaped():
    pipe = PipeHydraulics(0.03, liquid_head=670.0, holdup_beta=0.0175)
    q = np.linspace(0, 400, 401)
    g = np.array([pipe.dp2(x) for x in q])
    assert np.all(np.diff(g, 2) >= -1e-9)  # convex
    assert g[0] > g[40]  # liquid head dominates at turndown


def test_wellhead_curve_round_trip():
    curve = WellheadCurve.from_test(q=25.0, p_wh=40.0, p_ws=70.0, n=0.8)
    assert curve.rate(40.0) == pytest.approx(25.0)
    assert curve.wellhead_pressure(25.0) == pytest.approx(40.0)
    assert curve.rate(70.0) == 0.0


def test_bottomhole_fit_is_close_but_not_identical():
    well = BottomholeWell(c_r=0.004, n_r=0.8, p_res=180, column_s=0.18, friction_f=0.05)
    fit = well.fit_wellhead_curve(n=0.75)
    rates = [(well.rate_at_wellhead(p), fit.rate(p)) for p in (60, 90, 120)]
    assert all(t == pytest.approx(f, rel=0.08) for t, f in rates)


def test_turner_rate_is_a_few_mmscfd_for_4_inch_tubing():
    assert 1.5 < turner_min_rate(40.0, 45.0, 3.958) < 4.0


def test_compressor_capacity_rises_with_suction_and_falls_with_discharge(registry):
    train = registry.network.compressor.train.map
    assert train.capacity_mmscfd(30, 78, 31) > train.capacity_mmscfd(25, 78, 31)
    assert train.capacity_mmscfd(25, 85, 31) < train.capacity_mmscfd(25, 75, 31)
    assert train.capacity_mmscfd(25, 75, 34) < train.capacity_mmscfd(25, 75, 30)  # hot-day derate


def test_blending_is_molar_linear():
    a, b = Composition(0.30, 5, 40, 700), Composition(0.02, 1, 10, 1120)
    total, mix = blend([(10.0, a), (30.0, b)])
    assert total == 40.0
    assert mix.co2 == pytest.approx((10 * 0.30 + 30 * 0.02) / 40)
    assert mix.ghv_btu_scf == pytest.approx((10 * 700 + 30 * 1120) / 40)


def test_co2_removal_meets_spec_exactly():
    inlet = Composition(0.12, 3, 20, 950)
    removal = co2_removal_needed(q_in=400, inlet=inlet, co2_max=0.08, shrink_frac=0.04)
    sales = plant_outlet(q_in=400, inlet=inlet, co2_removed=removal, shrink_frac=0.04)
    assert sales.co2 == pytest.approx(0.08)
    assert sales.ghv_btu_scf > inlet.ghv_btu_scf  # removing an inert raises heating value


def test_hammerschmidt_meg_concentration():
    # dT = 1297 W / (62.07 (100 - W)) -> W ~ 19.5 wt% for 5 degC
    assert meg_weight_pct(5.0) == pytest.approx(19.37, abs=0.1)
    assert meg_weight_pct(-1.0) == 0.0


def test_choke_opening_grows_as_pressure_drop_shrinks():
    area = math.pi / 4 * (2.5 * 0.0254) ** 2
    wide = opening_pct(25, p_up=60, p_dn=40, max_area_m2=area, t_up_c=45)
    narrow = opening_pct(25, p_up=90, p_dn=40, max_area_m2=area, t_up_c=45)
    assert narrow < wide


def test_gas_day_calendar():
    cal = GasDayCalendar()
    start, end = cal.bounds_utc(date(2026, 11, 13))
    assert start == datetime(2026, 11, 12, 22, 0, tzinfo=UTC)  # 06:00 MYT
    assert (end - start).total_seconds() == 86_400
    assert cal.gas_day_of(datetime(2026, 11, 12, 21, 59, tzinfo=UTC)) == date(2026, 11, 12)
    assert cal.nomination_cutoff_utc(date(2026, 11, 13)) == datetime(2026, 11, 12, 3, 0, tzinfo=UTC)
