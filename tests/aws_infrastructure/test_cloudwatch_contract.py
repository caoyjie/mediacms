from .template_loader import load_template


CORE = "infra/aws/mediacms-core.yaml"
FORBIDDEN_RESOURCE_PREFIXES = (
    "AWS::SNS::",
    "AWS::CloudWatch::",
    "AWS::Events::",
    "AWS::SQS::",
)


def as_list(value):
    return value if isinstance(value, list) else [value]


def test_core_stack_has_no_aws_alerting_or_custom_monitoring_resources():
    template = load_template(CORE)
    resource_types = [resource["Type"] for resource in template["Resources"].values()]
    assert not any(
        resource_type.startswith(FORBIDDEN_RESOURCE_PREFIXES)
        for resource_type in resource_types
    )
    assert "AlarmNotificationEmail" not in template["Parameters"]


def test_core_stack_has_no_monitoring_lambda():
    resources = load_template(CORE)["Resources"]
    assert not any(
        resource["Type"] == "AWS::Lambda::Function" for resource in resources.values()
    )


def test_runtime_has_no_cloudwatch_metric_write_permission():
    template = load_template(CORE)
    statements = template["Resources"]["MediaCMSRuntimePolicy"]["Properties"][
        "PolicyDocument"
    ]["Statement"]
    actions = {
        action
        for statement in statements
        for action in as_list(statement["Action"])
    }
    assert "cloudwatch:PutMetricData" not in actions


def test_large_media_bucket_does_not_retain_noncurrent_object_versions():
    bucket = load_template(CORE)["Resources"]["MediaBucket"]
    assert "VersioningConfiguration" not in bucket["Properties"]
    assert bucket["Properties"]["LifecycleConfiguration"]["Rules"][0][
        "AbortIncompleteMultipartUpload"
    ]["DaysAfterInitiation"] == 1
    assert bucket["DeletionPolicy"] == "Retain"
    assert bucket["UpdateReplacePolicy"] == "Retain"
