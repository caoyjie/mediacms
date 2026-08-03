from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from files.models import MediaIngestionJob
from files.models.ingestion import JobStatus
from files.services.processing_queue import enqueue_job


def _job_payload(job):
    attempt = job.attempts.order_by("-sequence").first()
    checkpoints = []
    if attempt:
        checkpoints = list(attempt.checkpoints.order_by("created_at").values("name", "status", "evidence", "completed_at"))
    return {
        "job_id": str(job.id),
        "media_id": str(job.media_id) if job.media_id else None,
        "source_type": job.source_type,
        "title": job.media.title if job.media else job.media_title_snapshot,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "safe_error": job.safe_error,
        "cleanup_status": job.cleanup_status,
        "queued_at": job.queued_at,
        "updated_at": job.updated_at,
        "attempt_id": str(attempt.id) if attempt else None,
        "provider_status": attempt.provider_status if attempt else None,
        "provider_percent_complete": attempt.provider_percent_complete if attempt else None,
        "checkpoints": checkpoints,
    }


class AWSJobListView(APIView):
    permission_classes = (permissions.IsAdminUser,)

    def get(self, request):
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), 100)
        except (TypeError, ValueError):
            limit = 50
        jobs = MediaIngestionJob.objects.select_related("media").prefetch_related("attempts__checkpoints").order_by("-updated_at")[:limit]
        return Response({"results": [_job_payload(job) for job in jobs]})


class AWSJobDetailView(APIView):
    permission_classes = (permissions.IsAdminUser,)

    def get(self, request, job_id):
        job = MediaIngestionJob.objects.select_related("media").prefetch_related("attempts__checkpoints").filter(pk=job_id).first()
        if job is None:
            return Response({"detail": "Job not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(_job_payload(job))


class AWSJobActionView(APIView):
    permission_classes = (permissions.IsAdminUser,)

    def post(self, request, job_id, action):
        job = MediaIngestionJob.objects.filter(pk=job_id).first()
        if job is None:
            return Response({"detail": "Job not found."}, status=status.HTTP_404_NOT_FOUND)
        if action == "cancel":
            if job.status in {JobStatus.COMPLETED, JobStatus.CANCELED}:
                return Response({"detail": "Job is already terminal."}, status=status.HTTP_409_CONFLICT)
            MediaIngestionJob.objects.filter(pk=job.pk).update(cancel_requested=True, stage="cancel_requested")
            return Response({"job_id": str(job.id), "status": "cancel_requested"})
        if action == "resume":
            try:
                resumed = enqueue_job(job.id)
            except Exception as error:
                return Response({"detail": str(error)}, status=status.HTTP_409_CONFLICT)
            return Response({"job_id": str(resumed.id), "status": resumed.status, "stage": resumed.stage})
        return Response({"detail": "Unknown job action."}, status=status.HTTP_400_BAD_REQUEST)
