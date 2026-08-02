from pathlib import Path

from .template_loader import load_template


CORE = "infra/aws/mediacms-core.yaml"


def as_list(value):
    return value if isinstance(value, list) else [value]


def runtime_policy_statements(template):
    return template["Resources"]["MediaCMSRuntimePolicy"]["Properties"][
        "PolicyDocument"
    ]["Statement"]


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


def test_bucket_is_private_encrypted_unversioned_and_aborts_stale_multipart():
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
    assert "VersioningConfiguration" not in props
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


def test_runtime_credentials_use_conditional_ab_slots_and_secret_is_not_output():
    template = load_template(CORE)
    resources = template["Resources"]
    assert resources["RuntimeAccessKeyA"]["Condition"] == "CreateRuntimeAccessKeyA"
    assert resources["RuntimeAccessKeyB"]["Condition"] == "CreateRuntimeAccessKeyB"
    secret = resources["RuntimeCredentialsSecret"]
    assert secret["DeletionPolicy"] == "RetainExceptOnCreate"
    assert secret["UpdateReplacePolicy"] == "Retain"
    assert "RuntimeCredentialsSecretArn" in template["Outputs"]
    assert all("AccessKey" not in str(value) for value in template["Outputs"].values())
    secret_template = secret["Properties"]["SecretString"]["Sub"][0]
    assert set(__import__("json").loads(secret_template)) == {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
    }


def test_runtime_user_has_no_console_login_profile():
    user = load_template(CORE)["Resources"]["MediaCMSRuntimeUser"]
    assert user["Type"] == "AWS::IAM::User"
    assert "LoginProfile" not in user["Properties"]


def test_runtime_policy_has_no_administrative_or_cross_bucket_permissions():
    statements = runtime_policy_statements(load_template(CORE))
    actions = {
        action
        for statement in statements
        for action in as_list(statement["Action"])
    }
    assert "cloudformation:*" not in actions
    assert "secretsmanager:GetSecretValue" not in actions
    assert not any(
        action.startswith("iam:Create") or action.startswith("iam:Delete")
        for action in actions
    )
    assert not any(
        statement.get("Resource") == "*"
        and "s3:GetObject" in as_list(statement["Action"])
        for statement in statements
    )


def test_runtime_s3_permissions_are_limited_to_project_prefixes():
    statements = runtime_policy_statements(load_template(CORE))
    list_statement = next(item for item in statements if item["Sid"] == "ListMediaPrefixes")
    assert list_statement["Condition"]["StringLike"]["s3:prefix"] == [
        "uploads/*",
        "originals/*",
        "candidates/*",
        "system/defaults/*",
    ]
    object_statement = next(
        item for item in statements if item["Sid"] == "ManageMediaObjects"
    )
    assert len(object_statement["Resource"]) == 4
    assert all("/*" in item["Sub"] for item in object_statement["Resource"])


def test_runtime_can_pass_only_the_mediaconvert_service_role():
    statement = next(
        item
        for item in runtime_policy_statements(load_template(CORE))
        if item["Sid"] == "PassMediaConvertRole"
    )
    assert statement["Action"] == "iam:PassRole"
    assert statement["Resource"] == {"GetAtt": "MediaConvertServiceRole.Arn"}
    assert statement["Condition"]["StringEquals"]["iam:PassedToService"] == (
        "mediaconvert.amazonaws.com"
    )


def test_mediaconvert_role_reads_originals_and_writes_candidates_only():
    role = load_template(CORE)["Resources"]["MediaConvertServiceRole"]
    statements = role["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    object_statements = {
        item["Sid"]: item for item in statements if item["Sid"] in {"ReadOriginals", "WriteCandidates"}
    }
    assert object_statements["ReadOriginals"]["Action"] == "s3:GetObject"
    assert object_statements["ReadOriginals"]["Resource"]["Sub"].endswith(
        "/originals/*"
    )
    assert object_statements["WriteCandidates"]["Action"] == "s3:PutObject"
    assert object_statements["WriteCandidates"]["Resource"]["Sub"].endswith(
        "/candidates/*"
    )
