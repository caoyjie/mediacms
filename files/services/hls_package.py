import posixpath
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlsplit


MAX_HLS_FILES = 10_000
MAX_HLS_DEPTH = 16
MAX_HLS_FILE_SIZE = 2 * 1024**3
MAX_HLS_TOTAL_SIZE = 20 * 1024**3
MAX_HLS_COMPRESSION_RATIO = 100
MAX_MANIFEST_SIZE = 1024 * 1024

ALLOWED_HLS_SUFFIXES = {
    ".m3u8",
    ".ts",
    ".m4s",
    ".mp4",
    ".aac",
    ".vtt",
    ".srt",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}

_URI_ATTRIBUTE = re.compile(r'(?:^|[:,])URI=(?:"([^"]+)"|([^,]+))')


class UnsafeHlsPackage(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HlsInventoryEntry:
    path: str
    size: int
    compressed_size: int
    content_type: str
    checksum_sha256: str
    is_symlink: bool = False


@dataclass(frozen=True, slots=True)
class HlsInventory:
    entries: tuple[HlsInventoryEntry, ...]

    def by_path(self):
        return {entry.path: entry for entry in self.entries}


@dataclass(frozen=True, slots=True)
class HlsClosure:
    entry_manifest: str
    paths: tuple[str, ...]


def normalize_hls_path(path):
    if not isinstance(path, str) or not path:
        raise UnsafeHlsPackage("HLS path is empty.")
    if "\x00" in path or "\\" in path or path.startswith("/") or "//" in path:
        raise UnsafeHlsPackage("HLS path is not a safe relative POSIX path.")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise UnsafeHlsPackage("HLS path contains traversal or ambiguous components.")
    if len(parts) - 1 > MAX_HLS_DEPTH:
        raise UnsafeHlsPackage("HLS path exceeds the maximum directory depth.")
    return path


def validate_hls_inventory(entries):
    entries = tuple(entries)
    if not entries:
        raise UnsafeHlsPackage("HLS package inventory is empty.")
    if len(entries) > MAX_HLS_FILES:
        raise UnsafeHlsPackage("HLS package exceeds 10,000 files.")
    normalized = []
    seen = set()
    total_size = 0
    for raw_entry in entries:
        path = normalize_hls_path(raw_entry.path)
        if path in seen:
            raise UnsafeHlsPackage("HLS package contains a duplicate normalized path.")
        seen.add(path)
        if raw_entry.is_symlink:
            raise UnsafeHlsPackage("HLS package contains a symbolic link.")
        if PurePosixPath(path).suffix.lower() not in ALLOWED_HLS_SUFFIXES:
            raise UnsafeHlsPackage("HLS package contains an unsupported extension.")
        if raw_entry.size <= 0 or raw_entry.compressed_size <= 0:
            raise UnsafeHlsPackage("HLS entry sizes must be positive.")
        if raw_entry.size > MAX_HLS_FILE_SIZE:
            raise UnsafeHlsPackage("HLS entry exceeds the per-file size limit.")
        if raw_entry.size / raw_entry.compressed_size > MAX_HLS_COMPRESSION_RATIO:
            raise UnsafeHlsPackage("HLS entry exceeds the compression ratio limit.")
        total_size += raw_entry.size
        if total_size > MAX_HLS_TOTAL_SIZE:
            raise UnsafeHlsPackage("HLS package exceeds the 20 GiB expanded size limit.")
        normalized.append(
            HlsInventoryEntry(
                path=path,
                size=raw_entry.size,
                compressed_size=raw_entry.compressed_size,
                content_type=raw_entry.content_type,
                checksum_sha256=raw_entry.checksum_sha256,
                is_symlink=False,
            )
        )
    return HlsInventory(tuple(normalized))


def _local_reference(manifest_path, reference):
    reference = reference.strip()
    parsed = urlsplit(reference)
    non_local = (
        not reference,
        reference.startswith("//"),
        bool(parsed.scheme),
        bool(parsed.netloc),
        bool(parsed.query),
        bool(parsed.fragment),
    )
    if any(non_local):
        raise UnsafeHlsPackage("HLS manifest contains an external or non-local reference.")
    if "\\" in parsed.path or "\x00" in parsed.path:
        raise UnsafeHlsPackage("HLS manifest contains an unsafe reference.")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(manifest_path), parsed.path))
    if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
        raise UnsafeHlsPackage("HLS manifest reference escapes the package.")
    return normalize_hls_path(resolved)


def _manifest_references(manifest_path, body):
    if not isinstance(body, str):
        raise UnsafeHlsPackage("Every HLS manifest body must be UTF-8 text.")
    if len(body.encode("utf-8")) > MAX_MANIFEST_SIZE:
        raise UnsafeHlsPackage("An HLS manifest body exceeds 1 MiB.")
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise UnsafeHlsPackage("HLS manifest body must start with #EXTM3U.")
    references = []
    for line in lines[1:]:
        upper = line.upper()
        if upper.startswith("#EXT-X-KEY:") or upper.startswith("#EXT-X-SESSION-KEY:"):
            raise UnsafeHlsPackage("encrypted or DRM HLS packages are not supported.")
        if line.startswith("#"):
            match = _URI_ATTRIBUTE.search(line)
            if match:
                references.append(_local_reference(manifest_path, match.group(1) or match.group(2)))
            continue
        references.append(_local_reference(manifest_path, line))
    return tuple(references)


def validate_hls_manifests(inventory, manifest_bodies):
    entries = inventory.by_path()
    manifests = {path for path in entries if path.lower().endswith(".m3u8")}
    if not manifests:
        raise UnsafeHlsPackage("HLS package must contain a unique entry manifest.")
    if set(manifest_bodies) != manifests:
        raise UnsafeHlsPackage("A registered HLS manifest body is missing or unexpected.")

    references_by_manifest = {}
    referenced_manifests = set()
    for manifest_path in manifests:
        references = _manifest_references(manifest_path, manifest_bodies[manifest_path])
        missing = [path for path in references if path not in entries]
        if missing:
            raise UnsafeHlsPackage("HLS manifest references a missing package object.")
        references_by_manifest[manifest_path] = references
        referenced_manifests.update(path for path in references if path in manifests)

    roots = manifests - referenced_manifests
    if len(roots) != 1:
        raise UnsafeHlsPackage("HLS package does not have a unique entry manifest.")
    entry_manifest = roots.pop()
    closure = set()
    visited_manifests = set()
    pending = [entry_manifest]
    while pending:
        path = pending.pop()
        if path in visited_manifests:
            continue
        visited_manifests.add(path)
        closure.add(path)
        for reference in references_by_manifest.get(path, ()):
            closure.add(reference)
            if reference in manifests:
                pending.append(reference)
    return HlsClosure(entry_manifest=entry_manifest, paths=tuple(sorted(closure)))
