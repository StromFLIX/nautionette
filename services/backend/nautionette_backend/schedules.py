"""User-facing schedule definitions and Temporal calendar conversion."""

from __future__ import annotations

from datetime import time
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator
from temporalio.client import ScheduleCalendarSpec, ScheduleRange, ScheduleSpec

Weekday = Literal[
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]

_DAY_INDEX = {
    "sunday": 0,
    "monday": 1,
    "tuesday": 2,
    "wednesday": 3,
    "thursday": 4,
    "friday": 5,
    "saturday": 6,
}
_DAY_NAME = {index: name for name, index in _DAY_INDEX.items()}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]


class _ScheduleBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = Field(description="IANA timezone used for local clock times, for example Europe/Berlin.")
    input: dict[str, Any] = Field(
        default_factory=dict, description="Workflow input saved with every scheduled run."
    )

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        value = value.strip()
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone, such as Europe/Berlin") from exc
        return value


class HourlySchedule(_ScheduleBase):
    frequency: Literal["hourly"]
    minute: int = Field(default=0, ge=0, le=59, description="Minute after each hour when the workflow runs.")


class DailySchedule(_ScheduleBase):
    frequency: Literal["daily"]
    at: time = Field(description="Local time of day in HH:MM format.")


class WeeklySchedule(_ScheduleBase):
    frequency: Literal["weekly"]
    at: time = Field(description="Local time of day in HH:MM format.")
    days: list[Weekday] = Field(min_length=1, description="Days of the week when the workflow runs.")


class MonthlySchedule(_ScheduleBase):
    frequency: Literal["monthly"]
    at: time = Field(description="Local time of day in HH:MM format.")
    day: int = Field(ge=1, le=31, description="Day of the month when the workflow runs.")


ScheduleRequest = Annotated[
    HourlySchedule | DailySchedule | WeeklySchedule | MonthlySchedule,
    Field(discriminator="frequency"),
]


def temporal_spec(schedule: ScheduleRequest) -> ScheduleSpec:
    """Convert the API model to an explicit Temporal calendar."""
    if isinstance(schedule, HourlySchedule):
        calendar = ScheduleCalendarSpec(
            minute=[ScheduleRange(schedule.minute)],
            hour=[ScheduleRange(0, 23)],
        )
    else:
        fields: dict[str, Any] = {
            "second": [ScheduleRange(schedule.at.second)],
            "minute": [ScheduleRange(schedule.at.minute)],
            "hour": [ScheduleRange(schedule.at.hour)],
        }
        if isinstance(schedule, WeeklySchedule):
            fields["day_of_week"] = [ScheduleRange(_DAY_INDEX[day]) for day in schedule.days]
        elif isinstance(schedule, MonthlySchedule):
            fields["day_of_month"] = [ScheduleRange(schedule.day)]
        calendar = ScheduleCalendarSpec(**fields)
    return ScheduleSpec(calendars=[calendar], time_zone_name=schedule.timezone)


def _values(ranges: Any, low: int, high: int) -> set[int]:
    if not ranges:
        return set(range(low, high + 1))
    values: set[int] = set()
    for entry in ranges:
        start = int(getattr(entry, "start", low))
        end = int(getattr(entry, "end", 0)) or start
        step = int(getattr(entry, "step", 0)) or 1
        values.update(range(start, end + 1, step))
    return values


def _clock(hour: int, minute: int, second: int) -> str:
    value = f"{hour:02d}:{minute:02d}"
    return f"{value}:{second:02d}" if second else value


def _description(definition: dict[str, Any]) -> str:
    frequency = definition["frequency"]
    if frequency == "hourly":
        return f"Every hour at :{definition['minute']:02d}"
    if frequency == "daily":
        return f"Every day at {definition['at']}"
    if frequency == "monthly":
        return f"Monthly on day {definition['day']} at {definition['at']}"
    if frequency == "weekly":
        days = definition["days"]
        if days == _WEEKDAYS:
            label = "Weekdays"
        elif len(days) == 1:
            label = f"Every {days[0].title()}"
        else:
            label = ", ".join(day[:3].title() for day in days)
        return f"{label} at {definition['at']}"
    return "Custom schedule"


def schedule_definition(spec: Any) -> dict[str, Any]:
    """Recover a friendly definition from a Temporal schedule spec."""
    timezone = getattr(spec, "time_zone_name", None) or "UTC"
    calendars = getattr(spec, "calendars", None) or []
    advanced = (
        bool(getattr(spec, "intervals", None))
        or bool(getattr(spec, "cron_expressions", None))
        or bool(getattr(spec, "skip", None))
        or getattr(spec, "start_at", None) is not None
        or getattr(spec, "end_at", None) is not None
        or getattr(spec, "jitter", None) is not None
    )
    if len(calendars) != 1 or advanced or bool(getattr(calendars[0], "year", None)):
        return {"frequency": "custom", "timezone": timezone, "description": "Custom schedule"}

    calendar = calendars[0]
    seconds = _values(getattr(calendar, "second", None), 0, 59)
    minutes = _values(getattr(calendar, "minute", None), 0, 59)
    hours = _values(getattr(calendar, "hour", None), 0, 23)
    month_days = _values(getattr(calendar, "day_of_month", None), 1, 31)
    week_days = _values(getattr(calendar, "day_of_week", None), 0, 6)
    months = _values(getattr(calendar, "month", None), 1, 12)
    all_hours = set(range(24))
    all_month_days = set(range(1, 32))
    all_week_days = set(range(7))

    definition: dict[str, Any]
    if (
        len(minutes) == 1
        and hours == all_hours
        and seconds == {0}
        and month_days == all_month_days
        and week_days == all_week_days
        and months == set(range(1, 13))
    ):
        definition = {"frequency": "hourly", "minute": next(iter(minutes))}
    elif len(hours) == len(minutes) == len(seconds) == 1 and months == set(range(1, 13)):
        at = _clock(next(iter(hours)), next(iter(minutes)), next(iter(seconds)))
        if month_days == all_month_days and week_days == all_week_days:
            definition = {"frequency": "daily", "at": at}
        elif month_days == all_month_days and week_days != all_week_days:
            days = [_DAY_NAME[index] for index in range(1, 7) if index in week_days]
            if 0 in week_days:
                days.append("sunday")
            definition = {"frequency": "weekly", "at": at, "days": days}
        elif len(month_days) == 1 and week_days == all_week_days:
            definition = {"frequency": "monthly", "at": at, "day": next(iter(month_days))}
        else:
            definition = {"frequency": "custom"}
    else:
        definition = {"frequency": "custom"}

    definition["timezone"] = timezone
    definition["description"] = _description(definition)
    return definition


def schedule_summary(spec: Any, *, paused: bool = False, next_action_times: Any = ()) -> dict[str, Any]:
    result = schedule_definition(spec)
    next_runs = [value.isoformat() for value in list(next_action_times)[:5]]
    result.update(
        {
            "paused": paused,
            "next_run": next_runs[0] if next_runs else None,
            "next_runs": next_runs,
        }
    )
    return result
