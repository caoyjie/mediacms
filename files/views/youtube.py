from rest_framework import permissions, status
from rest_framework.parsers import FormParser, MultiPartParser, JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from files.models import MediaIngestionJob, MediaJobCheckpoint
from files.services.youtube_cookies import latest_cookie, store_cookies
from files.services.youtube_jobs import create_youtube_job, resume_youtube_job, start_youtube_job
from files.processing_tasks import discover_youtube_metadata


class YouTubeJobCreateView(APIView):
    permission_classes = (permissions.IsAdminUser,)
    parser_classes = (JSONParser,)

    def post(self, request):
        try:
            job = create_youtube_job(
                request.user,
                request.data.get("url", ""),
                title=request.data.get("title", "YouTube video"),
                idempotency_key=request.data.get("idempotency_key"),
            )
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        discover_youtube_metadata.apply_async(args=(str(job.id),), queue="youtube_metadata")
        return Response({"job_id": str(job.id), "media_id": str(job.media_id), "stage": job.stage}, status=status.HTTP_201_CREATED)


class YouTubeCookieStatusView(APIView):
    permission_classes = (permissions.IsAdminUser,)

    def get(self, request):
        cookie = latest_cookie()
        return Response({
            "available": cookie is not None,
            "uploaded_at": cookie.uploaded_at if cookie else None,
            "status": cookie.status if cookie else None,
            "warning": None if cookie else "No cookies have been uploaded; restricted videos may fail.",
        })


class YouTubeCookieUploadView(APIView):
    permission_classes = (permissions.IsAdminUser,)
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        uploaded = request.FILES.get("cookies")
        if uploaded is None:
            return Response({"detail": "cookies file is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            version = store_cookies(uploaded.read())
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"cookie_version_id": str(version.id), "uploaded_at": version.uploaded_at}, status=status.HTTP_201_CREATED)


class YouTubeJobResumeView(APIView):
    permission_classes = (permissions.IsAdminUser,)
    parser_classes = (JSONParser,)

    def post(self, request, job_id):
        try:
            job = resume_youtube_job(job_id, cookie_version_id=request.data.get("cookie_version_id"))
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"job_id": str(job.id), "stage": job.stage, "status": job.status})


class YouTubeJobStartView(APIView):
    permission_classes = (permissions.IsAdminUser,)
    parser_classes = (JSONParser,)

    def post(self, request, job_id):
        try:
            job = start_youtube_job(job_id, subtitle_languages=request.data.get("subtitle_languages"))
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"job_id": str(job.id), "stage": job.stage, "status": job.status})


class YouTubeJobDetailView(APIView):
    permission_classes = (permissions.IsAdminUser,)

    def get(self, request, job_id):
        job = MediaIngestionJob.objects.select_related("media").filter(pk=job_id, source_type="youtube").first()
        if job is None:
            return Response({"detail": "YouTube job not found."}, status=status.HTTP_404_NOT_FOUND)
        checkpoint = MediaJobCheckpoint.objects.filter(attempt__job=job, name="metadata").order_by("-created_at").first()
        subtitle_checkpoint = MediaJobCheckpoint.objects.filter(attempt__job=job, name="subtitles").order_by("-created_at").first()
        caption_tracks = (checkpoint.evidence or {}).get("caption_tracks", {}) if checkpoint else {}
        return Response({
            "job_id": str(job.id),
            "media_id": str(job.media_id) if job.media_id else None,
            "status": job.status,
            "stage": job.stage,
            "progress": job.progress,
            "safe_error": job.safe_error,
            "title": job.media.title if job.media else job.media_title_snapshot,
            "metadata": (job.source_metadata or {}).get("discovered") or (checkpoint.evidence if checkpoint else None),
            "metadata_checkpoint": checkpoint.status if checkpoint else "pending",
            "subtitle_status": subtitle_checkpoint.status if subtitle_checkpoint else "pending",
            "subtitle_options": [
                {"language": language, "kind": track.get("kind", "manual")}
                for language, track in caption_tracks.items()
            ],
            "import_requested": bool((job.source_metadata or {}).get("import_requested")),
        })
