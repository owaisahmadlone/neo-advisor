from __future__ import annotations

from datetime import date

from neo.harness.loop import run_scenario
from neo.harness.metrics import paired_uplift, violation_days
from tests.conftest import ROOT


def test_closed_loop_runs_and_advisor_stays_within_limits(tmp_path):
    records = run_scenario(ROOT / "configs" / "reference", tmp_path, "normal", days=3, start=date(2026, 11, 1), seed=7)
    assert len(records) == 3
    assert violation_days(records, "advisor") == 0
    assert all(r.advisory_status != "blocked" for r in records)
    uplift = paired_uplift(records, "hold", resamples=200)
    assert uplift.days == 3


def test_bad_critical_tag_blocks_the_advisory(tmp_path):
    records = run_scenario(ROOT / "configs" / "reference", tmp_path, "badtag", days=4, start=date(2026, 11, 1), seed=7)
    assert any(r.advisory_status == "blocked" for r in records)
