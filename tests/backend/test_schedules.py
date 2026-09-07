"""Friendly schedule requests and Temporal calendar conversion."""

from datetime import UTC, datetime

import pytest
from nautionette_backend.schedules import ScheduleRequest, schedule_summary, temporal_spec
from pydantic import TypeAdapter, ValidationError


def request(payload):
    return TypeAdapter(ScheduleRequest).validate_python(payload)


def test_daily_schedule_keeps_its_local_time_and_timezone():
    spec = temporal_spec(
        request(
            {
                "frequency": "daily",
                "at": "07:30",
                "timezone": "Europe/Berlin",
                "input": {"city": "Hamburg"},
            }
        )
    )
    summary = schedule_summary(
        spec, next_action_times=[datetime(2026, 9, 8, 5, 30, tzinfo=UTC)]
    )

    assert summary == {
        "frequency": "daily",
        "at": "07:30",
        "timezone": "Europe/Berlin",
        "description": "Every day at 07:30",
        "paused": False,
        "next_run": "2026-09-08T05:30:00+00:00",
        "next_runs": ["2026-09-08T05:30:00+00:00"],
    }


def test_weekly_schedule_uses_named_days_instead_of_cron_fields():
    spec = temporal_spec(
        request(
            {
                "frequency": "weekly",
                "at": "18:15",
                "days": ["monday", "wednesday", "friday"],
                "timezone": "America/New_York",
            }
        )
    )

    assert schedule_summary(spec)["description"] == "Mon, Wed, Fri at 18:15"
    assert schedule_summary(spec)["days"] == ["monday", "wednesday", "friday"]


def test_schedule_rejects_unknown_timezones():
    with pytest.raises(ValidationError, match="valid IANA timezone"):
        request({"frequency": "daily", "at": "08:00", "timezone": "Local time"})


def test_advanced_temporal_constraints_are_not_presented_as_a_simple_rule():
    spec = temporal_spec(request({"frequency": "daily", "at": "08:00", "timezone": "UTC"}))
    spec.end_at = datetime(2026, 12, 31, tzinfo=UTC)

    assert schedule_summary(spec)["description"] == "Custom schedule"