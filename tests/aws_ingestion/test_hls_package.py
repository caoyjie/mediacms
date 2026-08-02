import pytest

from files.services.hls_package import (
    HlsInventoryEntry,
    UnsafeHlsPackage,
    normalize_hls_path,
    validate_hls_inventory,
    validate_hls_manifests,
)


def entry(path, size=100, compressed_size=50, content_type="application/octet-stream", is_symlink=False):
    return HlsInventoryEntry(
        path=path,
        size=size,
        compressed_size=compressed_size,
        content_type=content_type,
        checksum_sha256="YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE=",
        is_symlink=is_symlink,
    )


@pytest.mark.parametrize(
    "path",
    ["", "/master.m3u8", "../master.m3u8", "a/../master.m3u8", "a\\b.ts", "a\x00b.ts", "a//b.ts"],
)
def test_unsafe_hls_paths_are_rejected(path):
    with pytest.raises(UnsafeHlsPackage):
        normalize_hls_path(path)


def test_safe_hls_path_is_preserved():
    assert normalize_hls_path("video/720p/segment-001.m4s") == "video/720p/segment-001.m4s"


@pytest.mark.parametrize(
    "entries,error",
    [
        ((entry("master.m3u8"), entry("master.m3u8")), "duplicate"),
        ((entry("link.ts", is_symlink=True),), "symbolic"),
        ((entry("script.js"),), "extension"),
        ((entry("huge.ts", size=2 * 1024**3 + 1),), "per-file"),
        ((entry("ratio.ts", size=101, compressed_size=1),), "compression ratio"),
        ((entry("empty.ts", size=0),), "positive"),
    ],
)
def test_inventory_rejects_unsafe_entries(entries, error):
    with pytest.raises(UnsafeHlsPackage, match=error):
        validate_hls_inventory(entries)


def test_inventory_rejects_file_count_depth_and_total_limits():
    with pytest.raises(UnsafeHlsPackage, match="10,000"):
        validate_hls_inventory(tuple(entry(f"{number}.ts") for number in range(10_001)))
    with pytest.raises(UnsafeHlsPackage, match="depth"):
        validate_hls_inventory((entry("/".join(["d"] * 17) + "/segment.ts"),))
    oversized = tuple(entry(f"{number}.ts", size=2 * 1024**3, compressed_size=2 * 1024**3) for number in range(11))
    with pytest.raises(UnsafeHlsPackage, match="20 GiB"):
        validate_hls_inventory(oversized)


def basic_inventory():
    return validate_hls_inventory(
        (
            entry("master.m3u8", content_type="application/vnd.apple.mpegurl"),
            entry("video/playlist.m3u8", content_type="application/vnd.apple.mpegurl"),
            entry("video/init.mp4", content_type="video/mp4"),
            entry("video/segment-1.m4s", content_type="video/iso.segment"),
            entry("captions/en.vtt", content_type="text/vtt"),
        )
    )


def test_manifest_validation_finds_unique_entry_and_dependency_closure():
    closure = validate_hls_manifests(
        basic_inventory(),
        {
            "master.m3u8": """#EXTM3U
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID=\"subs\",URI=\"captions/en.vtt\"
#EXT-X-STREAM-INF:BANDWIDTH=800000,SUBTITLES=\"subs\"
video/playlist.m3u8
""",
            "video/playlist.m3u8": """#EXTM3U
#EXT-X-MAP:URI=\"init.mp4\"
#EXTINF:4,
segment-1.m4s
#EXT-X-ENDLIST
""",
        },
    )

    assert closure.entry_manifest == "master.m3u8"
    assert closure.paths == (
        "captions/en.vtt",
        "master.m3u8",
        "video/init.mp4",
        "video/playlist.m3u8",
        "video/segment-1.m4s",
    )


@pytest.mark.parametrize(
    "manifest,error",
    [
        ("#EXTM3U\nhttps://example.com/segment.ts\n", "external"),
        ("#EXTM3U\n//cdn.example.com/segment.ts\n", "external"),
        ("#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"key.bin\"\nsegment.ts\n", "encrypted"),
        ("#EXTM3U\n#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES,URI=\"key.bin\"\n", "encrypted"),
    ],
)
def test_manifest_rejects_external_encrypted_and_drm_content(manifest, error):
    inventory = validate_hls_inventory(
        (
            entry("master.m3u8"),
            entry("segment.ts"),
        )
    )
    with pytest.raises(UnsafeHlsPackage, match=error):
        validate_hls_manifests(inventory, {"master.m3u8": manifest})


def test_manifest_rejects_missing_reference_and_ambiguous_roots():
    inventory = validate_hls_inventory((entry("master.m3u8"),))
    with pytest.raises(UnsafeHlsPackage, match="missing"):
        validate_hls_manifests(inventory, {"master.m3u8": "#EXTM3U\nmissing.ts\n"})

    ambiguous = validate_hls_inventory((entry("one.m3u8"), entry("two.m3u8")))
    with pytest.raises(UnsafeHlsPackage, match="unique entry"):
        validate_hls_manifests(
            ambiguous,
            {"one.m3u8": "#EXTM3U\n", "two.m3u8": "#EXTM3U\n"},
        )


def test_every_registered_manifest_requires_bounded_utf8_text():
    inventory = validate_hls_inventory((entry("master.m3u8"),))
    with pytest.raises(UnsafeHlsPackage, match="body"):
        validate_hls_manifests(inventory, {})
    with pytest.raises(UnsafeHlsPackage, match="1 MiB"):
        validate_hls_manifests(inventory, {"master.m3u8": "x" * (1024 * 1024 + 1)})
