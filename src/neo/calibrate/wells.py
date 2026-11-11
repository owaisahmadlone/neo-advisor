"""Well-curve calibration from accepted well tests (two-step RTO, step one).

p_ws comes from the registry's last survey with a linear decline; the exponent n is the
registry's field value; C is refitted so the curve passes through the latest accepted
test. Confidence falls with test age.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from neo.core.units import barg_to_bara
from neo.domain.registry import Well
from neo.physics.wells import WellheadCurve

BASE_SIGMA = 0.03
SIGMA_PER_DAY = 0.001
MAX_SIGMA = 0.20
STALE_TEST_DAYS = 60


@dataclass(frozen=True, slots=True)
class WellCalibration:
    curve: WellheadCurve
    sigma_rel: float
    test_age_days: float
    used_test: bool


def shut_in_pressure(well: Well, *, survey_date: date, on: date) -> float:
    days = max((on - survey_date).days, 0)
    return well.p_ws_bara - well.p_ws_decline_bar_per_day * days


def calibrate_well(well: Well, tests: pd.DataFrame, *, survey_date: date, cutoff: pd.Timestamp) -> WellCalibration:
    p_ws = shut_in_pressure(well, survey_date=survey_date, on=cutoff.date())
    accepted = tests.loc[(tests["well_id"] == well.id) & tests["accepted"] & (tests["test_start_utc"] <= cutoff)]
    nominal = WellheadCurve(well.c_wh, well.n_wh, p_ws)
    if accepted.empty:
        return WellCalibration(nominal, MAX_SIGMA, float("inf"), used_test=False)
    latest = accepted.sort_values("test_start_utc").iloc[-1]
    age = (cutoff - latest["test_start_utc"]).total_seconds() / 86_400
    sigma = min(BASE_SIGMA + SIGMA_PER_DAY * age, MAX_SIGMA)
    p_wh = barg_to_bara(float(latest["pwh_barg"]))
    q = float(latest["q_gas_mmscfd"])
    if q <= 0 or p_wh >= p_ws:
        return WellCalibration(nominal, MAX_SIGMA, age, used_test=False)
    return WellCalibration(WellheadCurve.from_test(q=q, p_wh=p_wh, p_ws=p_ws, n=well.n_wh), sigma, age, used_test=True)
