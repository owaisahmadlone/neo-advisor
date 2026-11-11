"""Gas-day calendar: maps UTC historian timestamps to contractual gas days.

The gas day runs from ``start_hour`` local time to the same hour next day. Both the
timezone and the start hour come from configuration because the client's PGU gas-day
definition is still an open question.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class GasDayCalendar:
    timezone: str = "Asia/Kuala_Lumpur"
    start_hour: int = 6
    nomination_cutoff_hour: int = 11  # local time on the day before the gas day

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def bounds_utc(self, gas_day: date) -> tuple[datetime, datetime]:
        """UTC start (inclusive) and end (exclusive) of a gas day."""
        start_local = datetime.combine(gas_day, time(self.start_hour), tzinfo=self.zone)
        start = start_local.astimezone(UTC)
        return start, start + timedelta(days=1)

    def gas_day_of(self, ts_utc: datetime) -> date:
        local = ts_utc.astimezone(self.zone)
        shifted = local - timedelta(hours=self.start_hour)
        return shifted.date()

    def nomination_cutoff_utc(self, gas_day: date) -> datetime:
        """Snapshot cut-off for planning ``gas_day``: noon-ish local time the day before."""
        cutoff_local = datetime.combine(
            gas_day - timedelta(days=1), time(self.nomination_cutoff_hour), tzinfo=self.zone
        )
        return cutoff_local.astimezone(UTC)
