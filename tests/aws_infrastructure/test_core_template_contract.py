from pathlib import Path

from .template_loader import load_template


CORE = "infra/aws/mediacms-core.yaml"


def test_core_template_has_required_parameters_and_no_secret_outputs():
    template = load_template(CORE)
    required = {
        "Environment",
        "ResourceNamePrefix",
        "MediaBucketName",
        "ApplicationOrigin",
        "RuntimeAccessKeyAEnabled",
        "RuntimeAccessKeyBEnabled",
        "RuntimeActiveAccessKeySlot",
        "CloudFrontPublicKeyCurrent",
        "CloudFrontPublicKeyNext",
        "EnableCustomDomain",
        "MediaDomainName",
        "AcmCertificateArn",
        "AlarmNotificationEmail",
        "AutomatedAbrEnabled",
        "AccelerationMode",
    }
    assert required <= template["Parameters"].keys()
    assert template["Parameters"]["Environment"]["AllowedValues"] == ["dev", "prod"]
    assert template["Parameters"]["RuntimeActiveAccessKeySlot"]["AllowedValues"] == [
        "A",
        "B",
    ]
    assert all("secretaccesskey" not in name.lower() for name in template["Outputs"])


def test_core_template_rejects_invalid_runtime_key_slot_combinations():
    template = load_template(CORE)
    assertions = template["Rules"]["RuntimeAccessKeySlotsAreValid"]["Assertions"]
    assert len(assertions) == 2


def test_core_template_enforces_environment_names_and_mvp_features():
    rules = load_template(CORE)["Rules"]
    assert "ResourceNamePrefixMatchesEnvironment" in rules
    assert "MvpFeaturesRemainDisabled" in rules
    assert len(rules["ResourceNamePrefixMatchesEnvironment"]["Assertions"]) == 1
    assert len(rules["MvpFeaturesRemainDisabled"]["Assertions"]) == 2


def test_core_template_fits_cloudformation_template_body_limit():
    assert Path(CORE).stat().st_size <= 51_200


def test_core_template_declares_only_non_secret_output_contract():
    outputs = set(load_template(CORE)["Outputs"])
    assert outputs == {
        "MediaBucketName",
        "MediaBucketArn",
        "MediaConvertServiceRoleArn",
        "VideoHlsJobTemplateName",
        "AudioHlsJobTemplateName",
        "MediaDistributionId",
        "MediaDistributionDomainName",
        "MediaKeyGroupId",
        "CloudFrontPublicKeyCurrentId",
        "CloudFrontPublicKeyNextId",
        "RuntimeUserName",
        "RuntimeCredentialsSecretArn",
    }
