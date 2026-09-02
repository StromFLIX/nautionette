"""Reading a Temporal schedule back as the cron expression that created it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from nautionette_backend.clients.temporal_server import _cron_of


def field(start, end=None, step=1):
    return SimpleNamespace(start=start, end=start if end is None else end, step=step)


def calendar(**fields):
    return SimpleNamespace(
        minute=fields.get("minute"),
        hour=fields.get("hour"),
        day_of_month=fields.get("day_of_month"),
        month=fields.get("month"),
        day_of_week=fields.get("day_of_week"),
    )


def spec(*, cron=None, calendars=None):
    return SimpleNamespace(cron_expressions=cron, calendars=calendars)


def test_an_expression_temporal_kept_is_returned_as_is():
    assert _cron_of(spec(cron=["0 8 * * 1"])) == "0 8 * * 1"


def test_a_schedule_without_a_spec_has_no_cron():
    assert _cron_of(None) is None
    assert _cron_of(spec()) is None


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"minute": [field(0)], "hour": [field(8)]}, "0 8 * * *"),
        ({"minute": [field(0)], "hour": [field(8)], "day_of_week": [field(1)]}, "0 8 * * 1"),
        ({"minute": [field(0, 59, 15)], "hour": [field(9, 17)]}, "*/15 9-17 * * *"),
        ({"minute": [field(0), field(30)]}, "0,30 * * * *"),
        ({"hour": [field(0, 23, 2)]}, "* */2 * * *"),
        ({"day_of_month": [field(1, 15, 7)]}, "* * 1-15/7 * *"),
    ],
)
def test_a_calendar_is_rendered_back_as_a_cron_expression(fields, expected):
    assert _cron_of(spec(calendars=[calendar(**fields)])) == expected


def test_a_field_that_covers_everything_is_a_star():
    assert _cron_of(spec(calendars=[calendar(minute=[field(0, 59)])])) == "* * * * *"
