"""Named test scenarios for the truth plant.

Days are 1-based gas days of the run: ``run_day=1`` is the run's first gas day. Times are
local clock times on that calendar date; ``None`` means the start of the gas day.
``capacity_removed`` is in the asset's native unit (trains for HUB-C, MMscfd for GPP,
1 for a well), matching ``events.csv``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from neo.core.gasday import GasDayCalendar
from neo.ingest.schema import NETWORK_TAGS

TruthEventType = Literal["train_trip", "train_maintenance", "well_failure", "co2_removal_derate", "plant_derate"]


def _run_date(start: date, run_day: int) -> date:
    if run_day < 1:
        raise ValueError(f"run_day is 1-based, got {run_day}")
    return start + timedelta(days=run_day - 1)


@dataclass(frozen=True, slots=True)
class ScenarioEvent:
    event_id: str
    asset_id: str
    type: TruthEventType
    run_day: int
    duration_days: float
    capacity_removed: float
    note: str
    local_time: time | None = None

    def resolve(self, calendar: GasDayCalendar, start: date) -> TimedEvent:
        day = _run_date(start, self.run_day)
        if self.local_time is None:
            begins = calendar.bounds_utc(day)[0]
        else:
            begins = datetime.combine(day, self.local_time, tzinfo=calendar.zone).astimezone(UTC)
        return TimedEvent(
            self.event_id,
            self.asset_id,
            self.type,
            begins,
            begins + timedelta(days=self.duration_days),
            self.capacity_removed,
            self.note,
        )


@dataclass(frozen=True, slots=True)
class TimedEvent:
    event_id: str
    asset_id: str
    type: TruthEventType
    start_utc: datetime
    end_utc: datetime
    capacity_removed: float
    note: str

    def active_at(self, ts_utc: datetime) -> bool:
        return self.start_utc <= ts_utc < self.end_utc


@dataclass(frozen=True, slots=True)
class BadTag:
    """A tag reported Bad for ``hours`` up to and including the cut-off for ``run_day``'s gas day."""

    tag: str
    run_day: int
    hours: float

    def window_utc(self, calendar: GasDayCalendar, start: date) -> tuple[datetime, datetime]:
        cutoff = calendar.nomination_cutoff_utc(_run_date(start, self.run_day))
        return cutoff - timedelta(hours=self.hours), cutoff


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    events: tuple[ScenarioEvent, ...] = ()
    bad_tags: tuple[BadTag, ...] = ()
    nomination_mean: float = 0.97
    nomination_sd: float = 0.03
    description: str = ""

    @classmethod
    def load(cls, name: str) -> Scenario:
        try:
            return SCENARIOS[name]
        except KeyError:
            raise ValueError(f"unknown scenario {name!r}; choose from {sorted(SCENARIOS)}") from None

    def timed_events(self, calendar: GasDayCalendar, start: date) -> tuple[TimedEvent, ...]:
        return tuple(e.resolve(calendar, start) for e in self.events)


SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in (
        Scenario("normal", description="No events; nominations around 97% of DCQ."),
        Scenario(
            "trip",
            events=(
                ScenarioEvent(
                    "EV-TRIP-01",
                    "HUB-C",
                    "train_trip",
                    run_day=3,
                    duration_days=4,
                    capacity_removed=1,
                    note="Train trip on high vibration",
                    local_time=time(8),
                ),
            ),
            description="One compressor train trips at 08:00 local on run day 3; back 4 days later.",
        ),
        Scenario(
            "co2cut",
            events=(
                ScenarioEvent(
                    "EV-CO2-01",
                    "GPP",
                    "co2_removal_derate",
                    run_day=3,
                    duration_days=5,
                    capacity_removed=8.0,
                    note="CO2 removal derated to 60% of 20 MMscfd (membrane fouling)",
                ),
            ),
            description="CO2 removal capacity at 60% from run day 3 for 5 days.",
        ),
        Scenario(
            "badtag",
            bad_tags=(BadTag(NETWORK_TAGS["suction_pressure"], run_day=4, hours=6),),
            description="HUBS.PRESS reads Bad Input for the 6 h before the cut-off for run day 4.",
        ),
    )
}
