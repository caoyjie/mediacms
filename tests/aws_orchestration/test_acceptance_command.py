import json
from pathlib import Path

import pytest

from files.management.commands.verify_mediaconvert_orchestration import (
    build_trim_command,
    prepare_fixture,
    temporary_fixture,
)


def test_video_and_audio_trim_commands_are_bounded_and_shell_free(tmp_path):
    video = build_trim_command("input video.mp4", str(tmp_path / "video.mp4"), "video")
    audio = build_trim_command("input audio.mp3", str(tmp_path / "audio.mp3"), "audio")

    assert video[:3] == ["ffmpeg", "-y", "-i"]
    assert video[video.index("-t") + 1] == "20"
    assert audio[audio.index("-t") + 1] == "30"
    assert "input video.mp4" in video
    assert all(isinstance(item, str) for item in video + audio)


def test_fixture_rejects_missing_symlink_and_non_file_sources(tmp_path):
    missing = tmp_path / "missing.mp4"
    with pytest.raises(ValueError, match="regular file"):
        prepare_fixture(missing, "video", tmp_path / "work", runner=lambda *args, **kwargs: None)

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    link = tmp_path / "link.mp4"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="regular file"):
        prepare_fixture(link, "video", tmp_path / "work2", runner=lambda *args, **kwargs: None)


def test_fixture_falls_back_to_compatibility_encoding_and_returns_probe(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if command[0] == "ffmpeg":
            if len(calls) == 1:
                raise RuntimeError("stream copy rejected")
            Path(command[-1]).write_bytes(b"derived")
            return None
        return type("Result", (), {"stdout": json.dumps({"format": {"duration": "20"}})})()

    result = prepare_fixture(source, "video", tmp_path / "work", runner=runner)

    assert result.path.read_bytes() == b"derived"
    assert result.duration_seconds == 20.0
    assert len([command for command in calls if command[0] == "ffmpeg"]) == 2
    assert all("shell" not in command for command in calls)


def test_temporary_fixture_removes_private_directory_after_failure(tmp_path):
    source = tmp_path / "source.mp3"
    source.write_bytes(b"source")
    captured = []

    def runner(command, **kwargs):
        if command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"derived")
            return None
        return type("Result", (), {"stdout": json.dumps({"format": {"duration": "30"}})})()

    with pytest.raises(RuntimeError, match="acceptance failed"):
        with temporary_fixture(source, "audio", tmp_path / "private", runner=runner) as fixture:
            captured.append(fixture.path.parent)
            raise RuntimeError("acceptance failed")

    assert captured and not captured[0].exists()
