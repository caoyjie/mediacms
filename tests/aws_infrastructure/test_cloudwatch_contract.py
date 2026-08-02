import json

from .template_loader import load_template


CORE = "infra/aws/mediacms-core.yaml"
ALARM_METRICS = {
    "MediaConvertJobsErroredAlarm": ("AWS/MediaConvert", "JobsErroredCount"),
    "MediaConvertJobsCanceledAlarm": ("AWS/MediaConvert", "JobsCanceled"),
    "QueueWaitTimeoutAlarm": ("MediaCMS/Processing", "QueueWaitTimeoutCount"),
    "ProcessingTimeoutAlarm": ("MediaCMS/Processing", "ProcessingTimeoutCount"),
    "BlackVideoAlarm": ("AWS/MediaConvert", "BlackVideoDetected"),
    "VideoPaddingAlarm": ("AWS/MediaConvert", "VideoPaddingInserted"),
}


def test_alert_topic_and_optional_email_subscription_are_declared():
    template = load_template(CORE)
    resources = template["Resources"]
    assert resources["MediaInfrastructureAlerts"]["Type"] == "AWS::SNS::Topic"
    subscription = resources["MediaInfrastructureEmailSubscription"]
    assert subscription["Type"] == "AWS::SNS::Subscription"
    assert subscription["Condition"] == "HasAlarmNotificationEmail"
    assert subscription["Properties"]["Protocol"] == "email"


def test_required_alarms_use_real_or_explicit_application_metrics():
    resources = load_template(CORE)["Resources"]
    for logical_id, (namespace, metric_name) in ALARM_METRICS.items():
        alarm = resources[logical_id]
        assert alarm["Type"] == "AWS::CloudWatch::Alarm"
        assert alarm["Properties"]["Namespace"] == namespace
        assert alarm["Properties"]["MetricName"] == metric_name
        assert alarm["Properties"]["TreatMissingData"] == "notBreaching"
        assert alarm["Properties"]["AlarmActions"] == [
            {"Ref": "MediaInfrastructureAlerts"}
        ]


def test_dashboard_covers_queue_time_output_minutes_and_quality_signals():
    dashboard = load_template(CORE)["Resources"]["MediaInfrastructureDashboard"]
    assert dashboard["Type"] == "AWS::CloudWatch::Dashboard"
    body = dashboard["Properties"]["DashboardBody"]["Sub"]
    parsed = json.loads(body)
    serialized = json.dumps(parsed)
    for metric in (
        "StandbyTime",
        "TranscodingTime",
        "SDOutputDuration",
        "HDOutputDuration",
        "UHDOutputDuration",
        "AudioOutputDuration",
        "QVBRAvgQualityHighBitrate",
        "QVBRMinQualityHighBitrate",
        "BlackVideoDetected",
        "VideoPaddingInserted",
    ):
        assert metric in serialized


def test_quality_alarms_are_warning_only():
    template = load_template(CORE)
    quality = [
        template["Resources"][name]
        for name in ("BlackVideoAlarm", "VideoPaddingAlarm")
    ]
    assert all(item["Type"] == "AWS::CloudWatch::Alarm" for item in quality)
    assert not any(
        resource["Type"] in {"AWS::Lambda::Function", "AWS::Events::Rule"}
        for resource in template["Resources"].values()
    )


def test_application_metrics_namespace_contains_no_sensitive_dimensions():
    resources = load_template(CORE)["Resources"]
    for logical_id in ("QueueWaitTimeoutAlarm", "ProcessingTimeoutAlarm"):
        props = resources[logical_id]["Properties"]
        assert props["Namespace"] == "MediaCMS/Processing"
        serialized = json.dumps(props).lower()
        assert all(
            forbidden not in serialized
            for forbidden in ("title", "url", "cookie", "access_key", "secret")
        )
