from dataclasses import asdict

from celery import shared_task
from files.services.processing_runner import reconcile_processing, run_processing_tick
import files.services.processing_runner as processing_runner


@shared_task(name="aws_processing_tick")
def aws_processing_tick(job_id=None):
    owner_token = f"aws-processing:{job_id or 'reconciler'}"
    result = run_processing_tick(owner_token)
    if result.scheduled_delay is not None and result.action != "idle":
        aws_processing_tick.apply_async(
            args=((result.job_id or job_id),),
            countdown=max(0, result.scheduled_delay),
        )
    return {
        "action": result.action,
        "job_id": result.job_id,
        "attempt_id": result.attempt_id,
        "scheduled_delay": result.scheduled_delay,
    }


@shared_task(name="reconcile_aws_processing")
def reconcile_aws_processing():
    processing_runner.aws_processing_tick = aws_processing_tick
    return asdict(reconcile_processing())
