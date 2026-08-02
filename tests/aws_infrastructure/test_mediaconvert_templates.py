import json

from .template_loader import load_template


CORE = "infra/aws/mediacms-core.yaml"
EXPECTED_VIDEO_OUTPUTS = {
    (1920, 1080): (8, 6_000_000),
    (1280, 720): (8, 4_000_000),
    (854, 480): (7, 1_000_000),
    (640, 360): (7, 700_000),
}


def resource(name):
    return load_template(CORE)["Resources"][name]


def settings_json(name):
    value = resource(name)["Properties"]["SettingsJson"]
    assert isinstance(value, str)
    return json.loads(value)


def hls_group(settings):
    return next(
        group
        for group in settings["OutputGroups"]
        if group["OutputGroupSettings"]["Type"] == "HLS_GROUP_SETTINGS"
    )


def video_outputs(settings):
    return [
        output
        for output in hls_group(settings)["Outputs"]
        if "VideoDescription" in output
    ]


def test_job_templates_are_versioned_environment_scoped_and_api_polled():
    video = resource("VideoHlsJobTemplate")
    audio = resource("AudioHlsJobTemplate")
    assert video["Type"] == audio["Type"] == "AWS::MediaConvert::JobTemplate"
    assert video["Properties"]["Name"] == {
        "Sub": "${ResourceNamePrefix}-video-hls-v1"
    }
    assert audio["Properties"]["Name"] == {
        "Sub": "${ResourceNamePrefix}-audio-hls-v1"
    }
    assert "Status" not in video["Properties"]
    assert "Status" not in audio["Properties"]
    assert "StatusUpdateInterval" not in video["Properties"]
    assert "StatusUpdateInterval" not in audio["Properties"]
    assert video["Properties"]["AccelerationSettings"]["Mode"] == "DISABLED"
    assert audio["Properties"]["AccelerationSettings"]["Mode"] == "DISABLED"


def test_video_template_uses_fixed_qvbr_ladder_and_auto_rotation():
    settings = settings_json("VideoHlsJobTemplate")
    assert settings["Inputs"][0]["VideoSelector"]["Rotate"] == "AUTO"
    assert hls_group(settings)["OutputGroupSettings"]["HlsGroupSettings"][
        "SegmentLength"
    ] == 4
    outputs = video_outputs(settings)
    actual = {
        (output["VideoDescription"]["Width"], output["VideoDescription"]["Height"]): (
            output["VideoDescription"]["CodecSettings"]["H264Settings"][
                "QvbrSettings"
            ]["QvbrQualityLevel"],
            output["VideoDescription"]["CodecSettings"]["H264Settings"][
                "MaxBitrate"
            ],
        )
        for output in outputs
    }
    assert actual == EXPECTED_VIDEO_OUTPUTS


def test_video_outputs_use_single_pass_qvbr_aac_without_max_average_bitrate():
    outputs = video_outputs(settings_json("VideoHlsJobTemplate"))
    for output in outputs:
        video = output["VideoDescription"]
        assert video["Width"] % 2 == 0
        assert video["Height"] % 2 == 0
        h264 = video["CodecSettings"]["H264Settings"]
        assert h264["RateControlMode"] == "QVBR"
        assert h264["QualityTuningLevel"] == "SINGLE_PASS_HQ"
        assert "MaxAverageBitrate" not in h264
        assert "MaxAverageBitrate" not in h264["QvbrSettings"]
        assert output["AudioDescriptions"][0]["CodecSettings"]["Codec"] == "AAC"


def test_video_template_has_supplementary_frame_capture_output():
    settings = settings_json("VideoHlsJobTemplate")
    groups = settings["OutputGroups"]
    assert any(
        output.get("VideoDescription", {})
        .get("CodecSettings", {})
        .get("Codec")
        == "FRAME_CAPTURE"
        for group in groups
        for output in group["Outputs"]
    )
    assert len(video_outputs(settings)) == 4


def test_audio_template_is_audio_only_apple_hls():
    settings = settings_json("AudioHlsJobTemplate")
    assert settings["Inputs"][0]["AudioSelectors"]
    group = hls_group(settings)
    assert group["OutputGroupSettings"]["HlsGroupSettings"]["SegmentLength"] == 4
    assert len(group["Outputs"]) == 1
    output = group["Outputs"][0]
    assert "VideoDescription" not in output
    assert output["AudioDescriptions"][0]["CodecSettings"]["Codec"] == "AAC"


def test_templates_do_not_enable_automated_abr():
    for name in ("VideoHlsJobTemplate", "AudioHlsJobTemplate"):
        serialized = json.dumps(settings_json(name))
        assert "AutomatedAbrSettings" not in serialized
