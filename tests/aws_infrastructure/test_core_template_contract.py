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


def test_bucket_is_private_encrypted_versioned_and_aborts_stale_multipart():
    bucket = load_template(CORE)["Resources"]["MediaBucket"]
    props = bucket["Properties"]
    assert bucket["DeletionPolicy"] == "Retain"
    assert bucket["UpdateReplacePolicy"] == "Retain"
    assert props["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True,
        "IgnorePublicAcls": True,
        "BlockPublicPolicy": True,
        "RestrictPublicBuckets": True,
    }
    assert props["OwnershipControls"]["Rules"] == [
        {"ObjectOwnership": "BucketOwnerEnforced"}
    ]
    assert props["VersioningConfiguration"]["Status"] == "Enabled"
    assert props["BucketEncryption"]["ServerSideEncryptionConfiguration"][0][
        "ServerSideEncryptionByDefault"
    ]["SSEAlgorithm"] == "AES256"
    rule = props["LifecycleConfiguration"]["Rules"][0]
    assert rule["AbortIncompleteMultipartUpload"]["DaysAfterInitiation"] == 1


def test_upload_cors_is_credentialed_and_limited_to_the_application_origin():
    template = load_template(CORE)
    cors = template["Resources"]["MediaBucket"]["Properties"][
        "CorsConfiguration"
    ]["CorsRules"][0]
    assert cors["AllowedOrigins"] == [{"Ref": "ApplicationOrigin"}]
    assert cors["AllowedMethods"] == ["PUT", "HEAD"]
    assert set(cors["ExposedHeaders"]) == {
        "ETag",
        "x-amz-checksum-crc32",
        "x-amz-checksum-crc32c",
        "x-amz-checksum-sha1",
        "x-amz-checksum-sha256",
    }

    response_policy = template["Resources"]["MediaCorsResponseHeadersPolicy"]
    cors_config = response_policy["Properties"]["ResponseHeadersPolicyConfig"][
        "CorsConfig"
    ]
    assert cors_config["AccessControlAllowCredentials"] is True
    assert cors_config["AccessControlAllowOrigins"]["Items"] == [
        {"Ref": "ApplicationOrigin"}
    ]


def test_cloudfront_uses_oac_key_group_and_no_public_s3_origin():
    template = load_template(CORE)
    resources = template["Resources"]
    distribution = resources["MediaDistribution"]["Properties"][
        "DistributionConfig"
    ]
    assert len(distribution["Origins"]) == 1
    assert distribution["DefaultCacheBehavior"]["TrustedKeyGroups"]
    assert distribution["Origins"][0]["OriginAccessControlId"]
    assert resources["MediaOAC"]["Properties"][
        "OriginAccessControlConfig"
    ]["SigningBehavior"] == "always"
    assert resources["MediaKeyGroup"]["Properties"]["KeyGroupConfig"]["Items"] == [
        {"Ref": "CloudFrontPublicKeyCurrentResource"},
        {"Ref": "CloudFrontPublicKeyNextResource"},
    ]

    statements = resources["MediaBucketPolicy"]["Properties"]["PolicyDocument"][
        "Statement"
    ]
    assert len(statements) == 1
    assert statements[0]["Principal"] == {"Service": "cloudfront.amazonaws.com"}
    assert statements[0]["Action"] == "s3:GetObject"
    assert "AWS:SourceArn" in statements[0]["Condition"]["StringEquals"]


def test_cloudfront_distribution_uses_private_viewer_and_low_cost_defaults():
    config = load_template(CORE)["Resources"]["MediaDistribution"]["Properties"][
        "DistributionConfig"
    ]
    behavior = config["DefaultCacheBehavior"]
    assert behavior["ViewerProtocolPolicy"] == "redirect-to-https"
    assert behavior["AllowedMethods"] == ["GET", "HEAD", "OPTIONS"]
    assert behavior["Compress"] is True
    assert config["HttpVersion"] == "http2and3"
    assert config["IPV6Enabled"] is True
    assert config["PriceClass"] == "PriceClass_100"


def test_custom_domain_rule_requires_domain_and_certificate_together():
    template = load_template(CORE)
    assertions = template["Rules"]["CustomDomainConfigurationIsComplete"][
        "Assertions"
    ]
    assert len(assertions) == 1
    distribution = template["Resources"]["MediaDistribution"]["Properties"][
        "DistributionConfig"
    ]
    assert distribution["Aliases"]["If"][0] == "UseCustomDomain"
    assert distribution["ViewerCertificate"]["If"][0] == "UseCustomDomain"
