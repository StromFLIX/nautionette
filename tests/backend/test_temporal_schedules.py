"""Reading a Temporal schedule back as the cron expression that created it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from nautionette_backend.clients.temporal_server import TemporalGateway, _cron_of
from nautionette_backend.schedules import DailySchedule, temporal_spec
from temporalio.client import ScheduleAlreadyRunningError


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


class ScheduleHandle:
    def __init__(self, schedule):
        self.schedule = schedule
        self.updates = 0

    async def update(self, updater):
        update = updater(SimpleNamespace(description=SimpleNamespace(schedule=self.schedule)))
        self.schedule = update.schedule
        self.updates += 1

    async def describe(self):
        return SimpleNamespace(
            schedule=self.schedule,
            info=SimpleNamespace(next_action_times=[]),
        )


class ScheduleClient:
    def __init__(self, error, existing):
        self.error = error
        self.handle = ScheduleHandle(existing)
        self.handle_requests = 0

    async def create_schedule(self, schedule_id, schedule):
        raise self.error

    def get_schedule_handle(self, schedule_id):
        self.handle_requests += 1
        return self.handle


class DataConverter:
    def __init__(self, payload):
        self.payload = payload
        self.decoded = None

    async def decode(self, values):
        self.decoded = values
        return [self.payload]


async def test_an_existing_schedule_is_updated_atomically(monkeypatch):
    gateway = TemporalGateway()
    client = ScheduleClient(ScheduleAlreadyRunningError(), SimpleNamespace())

    async def connect():
        return client

    monkeypatch.setattr(gateway, "client", connect)
    schedule = DailySchedule(frequency="daily", at="07:30", timezone="Europe/Berlin")
    result = await gateway.set_schedule("digest", temporal_spec(schedule), {})

    assert client.handle.updates == 1
    assert result["description"] == "Every day at 07:30"


async def test_a_create_failure_does_not_touch_the_existing_schedule(monkeypatch):
    gateway = TemporalGateway()
    client = ScheduleClient(RuntimeError("Temporal unavailable"), SimpleNamespace())

    async def connect():
        return client

    monkeypatch.setattr(gateway, "client", connect)
    schedule = DailySchedule(frequency="daily", at="07:30", timezone="Europe/Berlin")

    with pytest.raises(RuntimeError, match="Temporal unavailable"):
        await gateway.set_schedule("digest", temporal_spec(schedule), {})
    assert client.handle_requests == 0
    assert client.handle.updates == 0


async def test_describing_a_schedule_decodes_its_saved_workflow_input(monkeypatch):
    gateway = TemporalGateway()
    schedule = DailySchedule(frequency="daily", at="07:30", timezone="Europe/Berlin")
    converter = DataConverter({"url": "https://example.com"})
    description = SimpleNamespace(
        schedule=SimpleNamespace(
            action=SimpleNamespace(args=["encoded-payload"]),
            spec=temporal_spec(schedule),
            state=SimpleNamespace(paused=False),
        ),
        info=SimpleNamespace(next_action_times=[]),
        data_converter=converter,
    )
    handle = SimpleNamespace(describe=lambda: None)

    async def describe():
        return description

    handle.describe = describe
    client = SimpleNamespace(get_schedule_handle=lambda schedule_id: handle)

    async def connect():
        return client

    monkeypatch.setattr(gateway, "client", connect)
    result = await gateway.schedule("digest")

    assert result["input"] == {"url": "https://example.com"}
    assert converter.decoded == ["encoded-payload"]
