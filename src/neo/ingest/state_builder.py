"""Landing zone -> validated ``NetworkState`` for one gas day (and revision)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from neo.calibrate.pipes import PipeFit, fit_friction
from neo.calibrate.reconcile import Balance, reconcile
from neo.calibrate.wells import STALE_TEST_DAYS, WellCalibration, calibrate_well
from neo.core.units import barg_to_bara
from neo.domain.registry import Network
from neo.domain.state import DataFlag, NetworkState, OperatingPoint, WellState
from neo.ingest import schema
from neo.ingest.gate import GateConfig, TagReading, gate, read_all
from neo.ingest.landing import LandingZone

CALIBRATION_DAYS = 14
VFM_SIGMA_REL = 0.06


def _value(readings: dict[str, TagReading], tag: str) -> float | None:
    reading = readings.get(tag)
    return reading.mean if reading is not None and reading.usable else None


def _pressure(readings: dict[str, TagReading], tag: str) -> float | None:
    value = _value(readings, tag)
    return barg_to_bara(value) if value is not None else None


UNPLANNED_EVENTS = frozenset({"train_trip", "well_failure"})


def _active(
    events: pd.DataFrame, kinds: tuple[str, ...], start: pd.Timestamp, end: pd.Timestamp, *, cutoff: pd.Timestamp
) -> pd.DataFrame:
    """Events overlapping [start, end). Unplanned events count only once they have begun
    before the cut-off; planned ones (maintenance, derates) may be known in advance."""
    if events.empty:
        return events
    ends = events["end_utc"].fillna(pd.Timestamp.max.tz_localize("UTC"))
    known = ~events["type"].isin(UNPLANNED_EVENTS) | (events["start_utc"] <= cutoff)
    mask = events["type"].isin(kinds) & (events["start_utc"] < end) & (ends > start) & known
    return events.loc[mask]


@dataclass(slots=True)
class BuildTrace:
    """Everything ingest and calibration looked at and decided, for inspection (UI, audits)."""

    cutoff: pd.Timestamp
    day_start: pd.Timestamp
    day_end: pd.Timestamp
    readings: dict[str, TagReading] = field(default_factory=dict)
    calibrations: dict[str, WellCalibration] = field(default_factory=dict)
    raw_rates: dict[str, float] = field(default_factory=dict)
    route_gaps: dict[str, dict[str, float]] = field(default_factory=dict)
    hub_meter: float | None = None
    hub_wells: list[str] = field(default_factory=list)
    third_party_total: float = 0.0
    pipe_fits: dict[str, tuple[PipeFit, tuple[np.ndarray, np.ndarray, np.ndarray]]] = field(default_factory=dict)
    capacity_events: pd.DataFrame = field(default_factory=pd.DataFrame)


class _StateBuilder:
    def __init__(self, zone: LandingZone, gas_day: date, revision: int, cfg: GateConfig) -> None:
        self.zone = zone
        self.reg = zone.registry
        self.net: Network = self.reg.network
        self.gas_day = gas_day
        self.revision = revision
        self.cfg = cfg
        self.cutoff = pd.Timestamp(self.net.calendar.nomination_cutoff_utc(gas_day))
        start, end = self.net.calendar.bounds_utc(gas_day)
        self.day_start, self.day_end = pd.Timestamp(start), pd.Timestamp(end)
        self.flags: list[DataFlag] = []
        self.trace = BuildTrace(self.cutoff, self.day_start, self.day_end)

    def build(self) -> NetworkState:
        history = self.zone.historian(self.cutoff - timedelta(days=CALIBRATION_DAYS), self.cutoff)
        self.readings = read_all(history, self.cutoff, self.cfg, digital=self.zone.digital_tags)
        self.flags += gate(self.readings, self.zone.critical_tags, self.cutoff, self.cfg)
        self.trace.readings = self.readings
        self.wide = (
            history.loc[(history["quality"] == "Good") & history["value"].notna()]
            .pivot_table(index="ts_utc", columns="tag", values="value", aggfunc="mean")
            .sort_index()
        )
        third_party = self._third_party()
        wells = self._wells()
        routes = self._routes(wells)
        wells = self._reconcile(wells, routes, third_party)
        trains_available, trains_running = self._trains()
        suction = _pressure(self.readings, schema.NETWORK_TAGS["suction_pressure"])
        co2_cap, inlet_cap = self._plant_caps()
        return NetworkState(
            gas_day=self.gas_day,
            revision=self.revision,
            snapshot_cutoff_utc=self.cutoff.to_pydatetime(),
            wells=wells,
            current=OperatingPoint(
                routes=routes,
                trains_running=trains_running,
                suction_bara=suction
                if suction is not None
                else self.net.node_by_id[self.net.compressor.suction_node].p_min_bara,
            ),
            trains_available=trains_available,
            ambient_c=self._ambient(),
            third_party_rates=third_party,
            pipe_hydraulics=self._pipes(wells, routes),
            plant_inlet_cap_mmscfd=inlet_cap,
            co2_removal_cap_mmscfd=co2_cap,
            condensate_headroom_bbld=self._headroom(),
            nominations_mmbtu_d=self._nominations(),
            flags=tuple(self.flags),
        )

    # ------------------------------------------------------------------ wells
    def _wells(self) -> dict[str, WellState]:
        failures = _active(self.zone.events, ("well_failure",), self.cutoff, self.day_end, cutoff=self.cutoff)
        failed = set(failures["asset_id"])
        out = {}
        for well in self.net.wells:
            cal = calibrate_well(well, self.zone.well_tests, survey_date=self.net.valid_from, cutoff=self.cutoff)
            if not cal.used_test:
                self.flags.append(DataFlag("no_accepted_test", well.id, "using registry deliverability"))
            elif cal.test_age_days > STALE_TEST_DAYS:
                self.flags.append(
                    DataFlag("stale_test", well.id, f"last accepted test {cal.test_age_days:.0f} days old")
                )
            vfm = _value(self.readings, schema.well_tag(well.id, "VFM_GAS"))
            status = _value(self.readings, schema.well_tag(well.id, "STATUS"))
            online = (status >= 0.5) if status is not None else (vfm or 0.0) > 0.5
            if vfm is None:
                vfm = cal.curve.rate(40.0) if online else 0.0
                self.flags.append(
                    DataFlag("substituted", well.id, "rate estimate missing; using calibrated curve", "info")
                )
            available = well.id not in failed
            self.trace.calibrations[well.id] = cal
            self.trace.raw_rates[well.id] = vfm if online and available else 0.0
            out[well.id] = WellState(
                well_id=well.id,
                available=available,
                online=online and available,
                rate_mmscfd=vfm if online and available else 0.0,
                curve=cal.curve,
                sigma_rel=cal.sigma_rel,
                test_age_days=cal.test_age_days,
            )
        return out

    def _routes(self, wells: dict[str, WellState]) -> dict[str, str]:
        routes = {}
        for platform in self.net.platforms:
            options = platform.export_options
            if len(options) == 1:
                routes[platform.id] = options[0]
                continue
            measured = _pressure(self.readings, schema.NETWORK_TAGS["manifold_pressure"].format(node=platform.manifold))
            flow = sum(wells[w.id].rate_mmscfd for w in self.net.wells_on(platform.id))
            best, best_gap = options[0], float("inf")
            gaps = self.trace.route_gaps.setdefault(platform.id, {})
            for pipe_id in options:
                pipe = self.net.pipe_by_id[pipe_id]
                downstream = _pressure(self.readings, f"{pipe.to_node}.PRESS")
                if measured is None or downstream is None:
                    continue
                gap = abs(pipe.hydraulics.inlet_pressure(downstream, flow) - measured)
                gaps[pipe_id] = gap
                if gap < best_gap:
                    best, best_gap = pipe_id, gap
            routes[platform.id] = best
        return routes

    def _reconcile(
        self, wells: dict[str, WellState], routes: dict[str, str], third_party: dict[str, float]
    ) -> dict[str, WellState]:
        hub_meter = _value(self.readings, schema.NETWORK_TAGS["hub_inlet_flow"])
        if hub_meter is None:
            return wells
        comp = self.net.compressor
        hub_wells = [
            w.id for w in self.net.wells if self.net.pipe_by_id[routes[w.platform]].to_node == comp.suction_node
        ]
        third = sum(third_party.values())
        estimates = {w: s.rate_mmscfd for w, s in wells.items()}
        sigmas = {w: max(VFM_SIGMA_REL * s.rate_mmscfd, 0.05) for w, s in wells.items()}
        reconciled = reconcile(estimates, sigmas, [Balance("hub_inlet", hub_wells, hub_meter - third)])
        self.trace.hub_meter, self.trace.hub_wells, self.trace.third_party_total = hub_meter, hub_wells, third
        return {w: _with_rate(s, reconciled[w]) for w, s in wells.items()}

    # ---------------------------------------------------------------- network
    def _pipes(self, wells: dict[str, WellState], routes: dict[str, str]) -> dict:
        fitted = {}
        for pipe in self.net.pipes:
            prior = pipe.hydraulics
            series = self._pipe_series(pipe, wells, routes)
            if series is None:
                fitted[pipe.id] = prior
                continue
            fit = fit_friction(prior, *series)
            self.trace.pipe_fits[pipe.id] = (fit, series)
            if fit.clipped:
                self.flags.append(DataFlag("pipe_fit_clipped", pipe.id, "friction fit outside expected band"))
            fitted[pipe.id] = fit.hydraulics
        return fitted

    def _pipe_series(self, pipe, wells, routes) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        p_in_tag, p_out_tag = f"{pipe.from_node}.PRESS", f"{pipe.to_node}.PRESS"
        if pipe.parallel_group:
            flow_tag = schema.NETWORK_TAGS["trunk_flow"].format(pipe=pipe.id)
            needed = [p_in_tag, p_out_tag, flow_tag]
            if not set(needed) <= set(self.wide.columns):
                return None
            frame = self.wide[needed].dropna()
            q = frame[flow_tag].to_numpy()
        else:
            platform = next((p for p in self.net.platforms if pipe.id in p.export_options), None)
            if platform is None or len(platform.export_options) > 1:
                return None
            tags = [schema.well_tag(w.id, "VFM_GAS") for w in self.net.wells_on(platform.id)]
            needed = [p_in_tag, p_out_tag, *tags]
            if not set(needed) <= set(self.wide.columns):
                return None
            frame = self.wide[needed].dropna()
            q = frame[tags].sum(axis=1).to_numpy()
        return (
            frame[p_in_tag].to_numpy() + 1.01325,
            frame[p_out_tag].to_numpy() + 1.01325,
            q,
        )

    def _trains(self) -> tuple[int, int]:
        comp = self.net.compressor
        events = _active(
            self.zone.events, ("train_trip", "train_maintenance"), self.cutoff, self.day_end, cutoff=self.cutoff
        )
        down = round(events["capacity_removed"].astype(float).sum()) if not events.empty else 0
        self.trace.capacity_events = pd.concat([self.trace.capacity_events, events])
        available = max(comp.trains - down, 0)
        running = 0
        for n in range(1, comp.trains + 1):
            reading = self.readings.get(schema.NETWORK_TAGS["train_running"].format(n=n))
            if reading is not None and reading.last is not None:
                running += round(reading.last)
        if running > available:
            self.flags.append(
                DataFlag(
                    "train_status_mismatch", comp.id, f"{running} running but {available} available for the day", "info"
                )
            )
        return available, min(running, available)

    def _plant_caps(self) -> tuple[float, float]:
        plant = self.net.plant
        events = self.zone.events
        co2 = _active(events, ("co2_removal_derate",), self.cutoff, self.day_end, cutoff=self.cutoff)
        inlet = _active(events, ("plant_derate",), self.cutoff, self.day_end, cutoff=self.cutoff)
        self.trace.capacity_events = pd.concat([self.trace.capacity_events, co2, inlet])
        co2_removed = float(co2["capacity_removed"].astype(float).sum()) if not co2.empty else 0.0
        inlet_removed = float(inlet["capacity_removed"].astype(float).sum()) if not inlet.empty else 0.0
        return max(plant.co2_removal_cap_mmscfd - co2_removed, 0.0), max(plant.inlet_cap_mmscfd - inlet_removed, 0.0)

    def _third_party(self) -> dict[str, float]:
        schedule = self.zone.third_party_schedule
        out = {}
        for stream in self.net.third_party:
            rows = schedule.loc[(schedule["gas_day"] == self.gas_day) & (schedule["stream_id"] == stream.id)]
            if not rows.empty:
                out[stream.id] = float(rows["rate_mmscfd"].iloc[-1])
                continue
            measured = _value(self.readings, schema.NETWORK_TAGS["third_party_flow"].format(stream=stream.id))
            out[stream.id] = measured if measured is not None else stream.nominal_rate_mmscfd
            self.flags.append(DataFlag("third_party_estimated", stream.id, "no declared schedule; using measured flow"))
        return out

    def _ambient(self) -> float:
        value = _value(self.readings, schema.NETWORK_TAGS["ambient_temperature"])
        if value is None:
            self.flags.append(
                DataFlag("substituted", "AMBIENT", "ambient temperature missing; using design value", "info")
            )
            return self.net.compressor.design_ambient_c
        return value

    def _headroom(self) -> float:
        plan = self.zone.terminal_plan
        rows = plan.loc[plan["gas_day"] <= self.gas_day].sort_values("gas_day")
        if rows.empty:
            self.flags.append(DataFlag("substituted", "terminal", "no terminal plan; using slug-catcher limit"))
            return self.net.slug_catcher.liquid_cap_bbld
        if rows["gas_day"].iloc[-1] != self.gas_day:
            self.flags.append(
                DataFlag("substituted", "terminal", "terminal plan for the day missing; using latest", "info")
            )
        return float(rows["receiving_headroom_bbld"].iloc[-1])

    def _nominations(self) -> dict[str, float]:
        noms = self.zone.nominations
        rows = noms.loc[(noms["gas_day"] == self.gas_day) & (noms["submitted_utc"] <= self.cutoff)]
        out = {}
        for contract in self.reg.commercial.gsas:
            mine = rows.loc[rows["contract_id"] == contract.id].sort_values(["revision", "submitted_utc"])
            if mine.empty:
                out[contract.id] = contract.dcq_mmbtu_d
                self.flags.append(
                    DataFlag("nomination_missing", contract.id, "no nomination before cut-off; using DCQ")
                )
            else:
                out[contract.id] = float(mine["qty_mmbtu_d"].iloc[-1])
        return out


def _with_rate(state: WellState, rate: float) -> WellState:
    from dataclasses import replace

    return replace(state, rate_mmscfd=rate)


def build_state(zone: LandingZone, gas_day: date, *, revision: int = 0, cfg: GateConfig | None = None) -> NetworkState:
    return _StateBuilder(zone, gas_day, revision, cfg or GateConfig()).build()


def build_state_traced(
    zone: LandingZone, gas_day: date, *, revision: int = 0, cfg: GateConfig | None = None
) -> tuple[NetworkState, BuildTrace]:
    builder = _StateBuilder(zone, gas_day, revision, cfg or GateConfig())
    state = builder.build()
    return state, builder.trace
