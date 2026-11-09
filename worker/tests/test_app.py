from __future__ import annotations

from datetime import UTC, datetime, timedelta

from celery.schedules import crontab
from freezegun import freeze_time

import famguard_worker.tasks.ingestion  # noqa: F401  (registers tasks)
from app.tasks import EXPIRY_SWEEP_TASK, INGEST_TASK, RESUME_TASK
from famguard_worker.tasks.ingestion import retry_countdown
from famguard_worker.worker import app


def test_reliability_settings() -> None:
    conf = app.conf
    assert conf.task_serializer == "json"
    assert conf.accept_content == ["json"]
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.result_expires == 3600
    assert conf.broker_transport_options["visibility_timeout"] == 3600


def test_tasks_registered_under_shared_names() -> None:
    assert {INGEST_TASK, RESUME_TASK} <= set(app.tasks)


def test_beat_runs_expiry_sweep_daily_at_midnight() -> None:
    entry = app.conf.beat_schedule["daily-expiry-sweep"]
    schedule = entry["schedule"]
    assert entry["task"] == EXPIRY_SWEEP_TASK
    assert isinstance(schedule, crontab)
    assert schedule.hour == {0}
    assert schedule.minute == {0}
    assert app.conf.timezone == "UTC"


def test_beat_next_run_is_one_minute_after_2359() -> None:
    schedule = app.conf.beat_schedule["daily-expiry-sweep"]["schedule"]
    schedule.app = app
    with freeze_time("2026-10-18 23:59:00"):
        last_run = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)
        remaining = schedule.remaining_estimate(last_run)
    assert remaining == timedelta(minutes=1)


def test_retry_countdowns_back_off() -> None:
    assert [retry_countdown(n) for n in range(5)] == [10, 60, 300, 300, 300]
