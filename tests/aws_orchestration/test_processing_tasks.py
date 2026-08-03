import pytest

from files.processing_tasks import aws_processing_tick, reconcile_aws_processing
from files.services.processing_runner import ReconcileResult, TickResult


def test_tick_task_schedules_only_one_follow_up_without_sleep(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "files.processing_tasks.run_processing_tick",
        lambda owner_token: TickResult(
            "poll", "job-1", "attempt-1", scheduled_delay=30, terminal=False
        ),
    )
    monkeypatch.setattr(
        aws_processing_tick,
        "apply_async",
        lambda **kwargs: calls.append(kwargs),
    )

    result = aws_processing_tick.run("job-1")

    assert result == {
        "action": "poll",
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "scheduled_delay": 30,
    }
    assert calls == [{"args": ("job-1",), "countdown": 30}]


def test_reconciler_task_delegates_to_postgres_recovery(monkeypatch):
    monkeypatch.setattr(
        "files.processing_tasks.reconcile_processing",
        lambda: ReconcileResult(wakeups=2),
    )

    assert reconcile_aws_processing.run() == {"wakeups": 2}


@pytest.mark.parametrize("task_name", ("aws_processing_tick", "reconcile_aws_processing"))
def test_tasks_are_registered_with_stable_names(task_name):
    task = globals()[task_name]
    assert task.name == task_name
