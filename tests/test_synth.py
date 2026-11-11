from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from neo.core.ids import tree_hashes
from neo.domain.fixtures import DESIGN_MANIFOLD_BARA
from neo.domain.registry import Registry
from neo.ingest import schema
from neo.synth import Scenario, TruthPlant, generate
from neo.synth.extracts import EXTRACTS, TAG_MAP_FILE
from neo.synth.scenarios import ScenarioEvent
from neo.synth.truth import PeriodResult

REGISTRY_DIR = Path(__file__).resolve().parents[1] / "configs" / "reference"
START = date(2026, 11, 2)
DAYS = 5
SEED = 11


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load(REGISTRY_DIR)


def _landing(tmp_path_factory: pytest.TempPathFactory, scenario: str, name: str) -> Path:
    out = tmp_path_factory.mktemp(name)
    generate(REGISTRY_DIR, out, start=START, days=DAYS, scenario=scenario, seed=SEED)
    return out


@pytest.fixture(scope="module")
def normal_zone(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _landing(tmp_path_factory, "normal", "normal")


@pytest.fixture(scope="module")
def trip_zone(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _landing(tmp_path_factory, "trip", "trip")


@pytest.fixture(scope="module")
def badtag_zone(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _landing(tmp_path_factory, "badtag", "badtag")


def _historian(zone: Path) -> pd.DataFrame:
    files = sorted((zone / schema.HISTORIAN_DIR).glob("*.parquet"))
    return pd.concat([pd.read_parquet(p) for p in files], ignore_index=True)


def _plant(registry: Registry, scenario: str = "normal") -> TruthPlant:
    return TruthPlant(registry, seed=SEED, scenario=Scenario.load(scenario), start=START)


def test_same_seed_gives_byte_identical_landing_zone(normal_zone: Path, tmp_path: Path) -> None:
    generate(REGISTRY_DIR, tmp_path, start=START, days=DAYS, scenario="normal", seed=SEED)
    assert tree_hashes(tmp_path) == tree_hashes(normal_zone)


def test_different_seed_gives_different_data(normal_zone: Path, tmp_path: Path) -> None:
    generate(REGISTRY_DIR, tmp_path, start=START, days=2, scenario="normal", seed=SEED + 1)
    first_file = f"{schema.HISTORIAN_DIR}/{START.isoformat()}.parquet"
    assert tree_hashes(tmp_path)[first_file] != tree_hashes(normal_zone)[first_file]


def test_truth_differs_from_registry(registry: Registry) -> None:
    plant = _plant(registry)
    gaps = []
    for well in registry.network.wells:
        truth = plant.wells[well.id]
        fitted = truth.survey.fit_wellhead_curve()
        assert (fitted.c, fitted.n, fitted.p_ws) != (well.c_wh, well.n_wh, well.p_ws_bara)
        p_design = DESIGN_MANIFOLD_BARA[well.platform]
        gaps.append(abs(fitted.rate(p_design) / well.curve.rate(p_design) - 1))
    assert max(gaps) > 0.03, "true deliverability should not track the registry exactly"
    assert sum(gaps) / len(gaps) < 0.25, "but it should stay recognisably the same well"
    for pipe in registry.network.pipes:
        assert plant.pipes[pipe.id] != pipe.hydraulics
        if pipe.liquid_head_bar2 > 0:
            assert plant.pipes[pipe.id].liquid_head == pytest.approx(1.3 * pipe.hydraulics.liquid_head)


def test_cgr_falls_and_wgr_rises_with_depletion(registry: Registry) -> None:
    well = _plant(registry).wells["P2-W01"]
    assert well.cgr(300) < well.cgr(0)
    assert well.wgr(300) > well.wgr(0)


def test_respond_mode_clips_target_above_deliverability(registry: Registry) -> None:
    plant = _plant(registry)
    base = plant.initial_setpoints()
    greedy = replace(base, rates={**base.rates, "P2-W03": 10 * base.rates["P2-W03"]})
    result = plant.operate(START, greedy)
    period = result.periods[0]
    delivered = period.outcome.wells["P2-W03"]
    deliverability = period.physical.wells["P2-W03"].model.rate(delivered.manifold_bara)
    assert delivered.rate < greedy.rates["P2-W03"]
    assert delivered.rate == pytest.approx(deliverability, rel=1e-3)
    assert not [v for v in result.violations if v.limit == "well_deliverability"]


def test_operating_a_day_is_repeatable(registry: Registry) -> None:
    plant = _plant(registry)
    setpoints = plant.initial_setpoints()
    later = plant.operate(START + timedelta(days=3), setpoints)
    plant.operate(START, setpoints)
    again = plant.operate(START + timedelta(days=3), setpoints)
    assert later.well_rates == again.well_rates
    assert later.valuation.value == again.valuation.value


def test_trip_stops_a_train_and_cuts_hub_capacity(registry: Registry, trip_zone: Path) -> None:
    plant = _plant(registry, "trip")
    trip = plant.events[0]
    result = plant.operate(plant.calendar.gas_day_of(trip.start_utc), plant.initial_setpoints())
    before, after = result.period_at(trip.start_utc - timedelta(minutes=1)), result.period_at(trip.start_utc)

    def capacity(period: PeriodResult) -> float:
        out = period.outcome
        return period.setpoints.trains * period.physical.train.capacity_mmscfd(
            out.pressures["HUBS"], out.pressures["HUBD"], period.physical.ambient_c
        )

    assert after.setpoints.trains == before.setpoints.trains - 1
    assert capacity(after) < capacity(before)

    run = _historian(trip_zone).query("tag == 'HUB-C.T3.RUN' and quality == 'Good'")
    ts = run["ts_utc"]
    assert (run.loc[ts < trip.start_utc, "value"].tail(4) == 1.0).all()
    assert (run.loc[(ts >= trip.start_utc) & (ts < trip.end_utc), "value"] == 0.0).all()
    events = pd.read_csv(trip_zone / "events.csv")
    assert events.loc[0, "type"] == "train_trip"
    assert events.loc[0, "capacity_removed"] == 1.0


def test_co2_cut_derates_removal_capacity(registry: Registry) -> None:
    plant = _plant(registry, "co2cut")
    event = plant.events[0]
    full = plant.physical_network(event.start_utc - timedelta(hours=1)).co2_removal_cap
    cut = plant.physical_network(event.start_utc).co2_removal_cap
    assert cut == pytest.approx(0.6 * full)


def test_failed_well_stays_shut_and_plant_derate_applies(registry: Registry) -> None:
    scenario = Scenario(
        "failure",
        events=(
            ScenarioEvent("EV-W", "P1-W03", "well_failure", run_day=1, duration_days=2, capacity_removed=1, note="ESD"),
            ScenarioEvent("EV-P", "GPP", "plant_derate", run_day=1, duration_days=2, capacity_removed=100, note="Trip"),
        ),
    )
    plant = TruthPlant(registry, seed=SEED, scenario=scenario, start=START)
    setpoints = plant.initial_setpoints()
    assert setpoints.rates["P1-W03"] == 0.0
    result = plant.operate(START, replace(setpoints, rates={**setpoints.rates, "P1-W03": 20.0}))
    assert result.well_rates["P1-W03"] == 0.0
    assert result.periods[0].physical.plant_inlet_cap == registry.network.plant.inlet_cap_mmscfd - 100


def test_badtag_marks_suction_pressure_bad_before_cutoff(registry: Registry, badtag_zone: Path) -> None:
    plant = _plant(registry, "badtag")
    bad_tag = plant.scenario.bad_tags[0]
    start, cutoff = bad_tag.window_utc(plant.calendar, START)
    assert cutoff == plant.calendar.nomination_cutoff_utc(START + timedelta(days=3))
    samples = _historian(badtag_zone).query("tag == 'HUBS.PRESS'")
    inside = samples.loc[(samples["ts_utc"] >= start) & (samples["ts_utc"] <= cutoff)]
    assert len(inside) == 25
    assert (inside["quality"] == "Bad").all()
    assert (inside["digital_state"] == "Bad Input").all()
    assert inside["value"].isna().all()
    assert (samples.loc[samples["ts_utc"] > cutoff, "quality"].head(8) == "Good").any()


def test_landing_zone_matches_schema(normal_zone: Path) -> None:
    for path in sorted((normal_zone / schema.HISTORIAN_DIR).glob("*.parquet")):
        assert tuple(pq.read_schema(path).names) == schema.HISTORIAN_COLUMNS
    history = _historian(normal_zone)
    assert str(history["ts_utc"].dt.tz) == "UTC"
    assert history["value"].dtype == "float64"
    assert history["substituted"].dtype == bool
    assert set(history["digital_state"]) == {"", "Bad Input"}
    assert set(history["quality"]) <= {"Good", "Questionable", "Bad"}
    for name, (columns, _) in EXTRACTS.items():
        assert tuple(pd.read_csv(normal_zone / name).columns) == columns, name
    tag_map = pd.read_csv(normal_zone / TAG_MAP_FILE)
    assert tuple(tag_map.columns) == schema.TAG_MAP_COLUMNS
    assert set(history["tag"]) == set(tag_map["tag"])
    for name in ("asset_registry.yaml", "contracts.yaml", "psc_terms.yaml"):
        assert (normal_zone / schema.REGISTERS_DIR / name).read_bytes() == (REGISTRY_DIR / name).read_bytes()


def test_tags_follow_the_naming_contract(registry: Registry, normal_zone: Path) -> None:
    tag_map = pd.read_csv(normal_zone / TAG_MAP_FILE).set_index("tag")
    critical = set(tag_map.index[tag_map["critical"]])
    tags = schema.NETWORK_TAGS
    expected_critical = {
        tags["suction_pressure"],
        tags["discharge_pressure"],
        tags["arrival_pressure"],
        tags["hub_inlet_flow"],
        tags["plant_inlet_co2"],
        *(tags["train_running"].format(n=n) for n in range(1, registry.network.compressor.trains + 1)),
        *(tags["third_party_flow"].format(stream=s.id) for s in registry.network.third_party),
    }
    assert critical == expected_critical
    for well in registry.network.wells:
        for attribute in schema.WELL_ATTRIBUTES:
            assert schema.well_tag(well.id, attribute) in tag_map.index
    path = _historian(normal_zone).query("tag == 'P1-W01.PWH'")["af_path"].iloc[0]
    assert path == "\\\\NeO\\P1\\P1-W01|PWH"


def test_every_well_has_an_accepted_test_before_the_start(registry: Registry, normal_zone: Path) -> None:
    tests = pd.read_csv(normal_zone / "well_tests.csv", parse_dates=["test_start_utc"])
    run_start = pd.Timestamp(registry.network.calendar.bounds_utc(START)[0])
    before = tests.loc[tests["accepted"] & (tests["test_start_utc"] < run_start)]
    assert set(before["well_id"]) == {w.id for w in registry.network.wells}


def test_day_ahead_extracts_are_published_the_day_before(registry: Registry, normal_zone: Path) -> None:
    nominations = pd.read_csv(normal_zone / "nominations.csv", parse_dates=["submitted_utc"])
    calendar = registry.network.calendar
    for row in nominations.itertuples():
        gas_day = date.fromisoformat(row.gas_day)
        assert row.submitted_utc < calendar.nomination_cutoff_utc(gas_day)
    assert set(nominations["gas_day"]) == {(START + timedelta(days=d)).isoformat() for d in range(DAYS + 1)}
    dor = pd.read_csv(normal_zone / "dor_allocation.csv")
    assert set(dor["gas_day"]) == {(START + timedelta(days=d)).isoformat() for d in range(DAYS - 1)}
