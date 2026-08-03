"""YouTube control-plane helpers. Heavy bytes stay in the attempt temp dir."""

from dataclasses import dataclass
from pathlib import Path
import re
from urllib.request import Request, urlopen
from urllib.parse import parse_qs, urlparse

from django.conf import settings


@dataclass(frozen=True, slots=True)
class CaptionTrack:
    url: str
    language: str
    kind: str


def normalize_youtube_url(value):
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc.lower().split(":", 1)[0]
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        raise ValueError("URL is not a YouTube video")
    query = parse_qs(parsed.query)
    if host == "youtu.be":
        video_id = parsed.path.strip("/")
    else:
        video_id = query.get("v", [""])[0]
        if parsed.path.startswith("/playlist") or query.get("list") and not video_id:
            raise ValueError("only a single video is supported")
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,}", video_id or ""):
        raise ValueError("URL is not a valid single video")
    if query.get("list") and not video_id:
        raise ValueError("only a single video is supported")
    return video_id


def choose_caption_tracks(raw):
    def choose(language):
        candidates = []
        for code, values in (raw or {}).items():
            if code == language or code.lower().split("-")[0] == language:
                for item in values or []:
                    candidates.append(CaptionTrack(item["url"], language, item.get("kind", "manual")))
        candidates.sort(key=lambda track: (track.kind != "manual", track.url))
        return candidates[0] if candidates else None
    return {language: track for language in ("zh", "en") if (track := choose(language))}


def discovered_caption_tracks(info):
    """Return manual and automatic captions without allowing automatic tracks to overwrite manual ones."""
    inventory = {}
    for code, values in (info.get("subtitles") or {}).items():
        inventory.setdefault(code, []).extend({**item, "kind": "manual"} for item in values or [])
    for code, values in (info.get("automatic_captions") or {}).items():
        inventory.setdefault(code, []).extend({**item, "kind": "automatic"} for item in values or [])
    return inventory


def fetch_caption_text(track, *, opener=None, cookie_file=None):
    if not isinstance(track, CaptionTrack) or not track.url.startswith(("https://", "http://")):
        raise ValueError("caption URL is invalid")
    request = Request(track.url, headers={"User-Agent": "MediaCMS/1.0"})
    if opener is None and cookie_file is not None:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "cookiefile": str(cookie_file)}) as downloader:
            response = downloader.urlopen(request)
            payload = response.read(4 * 1024 * 1024 + 1)
    else:
        with (opener or urlopen)(request, timeout=30) as response:
            payload = response.read(4 * 1024 * 1024 + 1)
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError("caption file is too large")
    return payload.decode("utf-8-sig")


def classify_ytdlp_error(message):
    lowered = str(message).lower()
    if any(term in lowered for term in ("sign in", "age", "login", "authentication", "confirm your country", "cookies are no longer valid", "provided youtube account cookies")):
        return "cookies"
    if any(term in lowered for term in ("no subtitles", "there are no subtitles", "subtitles are not available")):
        return "unavailable"
    if any(term in lowered for term in ("429", "timed out", "temporarily", "connection reset", "javascript challenge", "n challenge", "only images are available", "ejs")):
        return "retryable"
    return "unknown"


def extract_info(url, *, cookie_file=None):
    import yt_dlp
    options = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
    }
    remote_components = getattr(settings, "YTDLP_REMOTE_COMPONENTS", ())
    if remote_components:
        options["remote_components"] = list(remote_components)
    if cookie_file:
        options["cookiefile"] = str(cookie_file)
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=False)
    if info.get("_type") == "playlist" or info.get("entries"):
        raise ValueError("only a single video is supported")
    return info


def download_source(url, output_dir, *, cookie_file=None):
    import yt_dlp
    target = Path(output_dir) / "source.%(ext)s"
    options = {
        "quiet": True,
        "noplaylist": True,
        "format": "bestvideo*+bestaudio/best",
        "outtmpl": str(target),
        "merge_output_format": "mp4",
    }
    remote_components = getattr(settings, "YTDLP_REMOTE_COMPONENTS", ())
    if remote_components:
        options["remote_components"] = list(remote_components)
    if cookie_file:
        options["cookiefile"] = str(cookie_file)
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
            path = Path(downloader.prepare_filename(info))
            if not path.exists():
                path = next(Path(output_dir).glob("source.*"))
            return path, info
    except Exception as error:
        kind = classify_ytdlp_error(str(error))
        error.kind = kind
        raise
