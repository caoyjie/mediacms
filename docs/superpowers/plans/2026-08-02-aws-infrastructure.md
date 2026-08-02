# MediaCMS AWS Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and validate the minimum independent MediaCMS AWS foundation in `us-east-1`: private S3, least-privilege runtime credentials, MediaConvert HLS templates and private CloudFront playback, then deploy a reviewed dev Stack only after an explicit execution gate.

**Architecture:** One production-safe core CloudFormation template owns only required storage, IAM, MediaConvert and CloudFront resources. Django later reconciles the single active MediaConvert job through `GetJob`; no SNS, CloudWatch alarm/dashboard/custom metric, EventBridge, SQS or monitoring Lambda is provisioned. A separate optional certificate template isolates the Cloudflare DNS validation gate.

**Tech Stack:** AWS CloudFormation YAML, S3, IAM, Secrets Manager, MediaConvert, CloudFront OAC/Key Groups, Bash, AWS CLI v2, cfn-lint, pytest/PyYAML.

## Global Constraints

- Region is exactly `us-east-1`; every AWS CLI command uses `--profile default --region us-east-1`.
- AWS resources are created, updated and deleted only through CloudFormation Stack/Change Set operations.
- The default production bucket physical name is `mediacms-${AWS::AccountId}-us-east-1`; dev must pass an account-specific override ending in `-dev`.
- All resources use `Project=mediacms` and `Environment=dev|prod` tags where the resource type supports tags.
- Runtime credentials use a dedicated IAM User, CloudFormation-managed A/B AccessKeys, and a Secrets Manager Secret. No `SecretAccessKey` appears in Stack Outputs, logs, committed files or command lines.
- The administrator `default` profile is never copied or mounted into application containers.
- Runtime permissions are restricted to the MediaCMS bucket/prefixes, MediaConvert jobs/templates and the exact MediaConvert service role.
- S3 remains private. CloudFront OAC is the only playback read path; signed cookies trust the configured Key Group.
- Production video/audio templates are `mediacms-video-hls-v1` and `mediacms-audio-hls-v1`; dev uses `mediacms-dev-video-hls-v1` and `mediacms-dev-audio-hls-v1`. Application video template version is `h264-hls-qvbr-v1`, H.264 uses `SINGLE_PASS_HQ + QVBR`, and `MaxAverageBitrate` is absent.
- QVBR ladder is 1080p level 8/max 6,000,000; 720p level 8/max 4,000,000; 480p level 7/max 1,000,000; 360p level 7/max 700,000. Orchestration later removes outputs above source resolution.
- Input rotation is `AUTO`; Automated ABR and accelerated transcoding are disabled.
- Audio template is `mediacms-audio-hls-v1` and produces audio-only Apple HLS.
- Cloudflare is not required for the default CloudFront domain. Custom ACM/domain deployment remains gated until the real domain and DNS approval are available.
- Production bucket and credential Secret are retained on Stack deletion. Stack deletion, retained-resource cleanup and old AWS resource cleanup require separate approval.
- S3 Versioning is not enabled. Attempt-specific immutable keys and `MediaAssetVersion` provide version isolation without retaining complete noncurrent copies of large objects.
- No SNS, CloudWatch alarm/dashboard/custom metric, EventBridge, SQS or monitoring Lambda resource is created.
- Large network downloads are not run by Codex. No new package download is required by this plan.

---

### Task 1: CloudFormation Contract Harness and Core Skeleton

**Files:**
- Create: `infra/aws/mediacms-core.yaml`
- Create: `tests/aws_infrastructure/__init__.py`
- Create: `tests/aws_infrastructure/template_loader.py`
- Create: `tests/aws_infrastructure/test_core_template_contract.py`

**Interfaces:**
- Produces: `load_template(path: str) -> dict`, a PyYAML loader that preserves CloudFormation intrinsic structures sufficiently for semantic assertions.
- Produces template parameters: `Environment`, `ResourceNamePrefix`, `MediaBucketName`, `ApplicationOrigin`, `RuntimeAccessKeyAEnabled`, `RuntimeAccessKeyBEnabled`, `RuntimeActiveAccessKeySlot`, `CloudFrontPublicKeyCurrent`, `CloudFrontPublicKeyNext`, `EnableCustomDomain`, `MediaDomainName`, `AcmCertificateArn`, `AutomatedAbrEnabled`, and `AccelerationMode`.
- Produces non-secret outputs: bucket name/ARN, MediaConvert role ARN and template names, CloudFront distribution ID/domain, Key Group/Public Key IDs, runtime user name, and runtime Secret ARN.

- [ ] **Step 1: Write the failing template contract tests**

Create a CloudFormation-aware YAML loader that converts any `!Ref`, `!Sub`, `!GetAtt`, `!If`, `!Equals`, `!And`, `!Or`, and `!Not` tag to a stable `{tag: value}` mapping. Add tests asserting:

```python
def test_core_template_has_required_parameters_and_no_secret_outputs():
    template = load_template("infra/aws/mediacms-core.yaml")
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
    assert template["Parameters"]["RuntimeActiveAccessKeySlot"]["AllowedValues"] == ["A", "B"]
    assert all("secretaccesskey" not in name.lower() for name in template["Outputs"])


def test_core_template_rejects_invalid_runtime_key_slot_combinations():
    template = load_template("infra/aws/mediacms-core.yaml")
    assertions = template["Rules"]["RuntimeAccessKeySlotsAreValid"]["Assertions"]
    assert len(assertions) == 2
```

- [ ] **Step 2: Run tests and verify the template is missing**

Run: `.venv/bin/pytest tests/aws_infrastructure/test_core_template_contract.py -q`

Expected: FAIL because `infra/aws/mediacms-core.yaml` does not exist.

- [ ] **Step 3: Create the core template skeleton**

Add the required parameters with exact allowed values and patterns:

```yaml
Environment:
  Type: String
  AllowedValues: [dev, prod]
ResourceNamePrefix:
  Type: String
  Default: mediacms
  AllowedValues: [mediacms, mediacms-dev]
MediaBucketName:
  Type: String
  Default: ''
  AllowedPattern: '^$|^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$'
ApplicationOrigin:
  Type: String
  AllowedPattern: '^(https://[A-Za-z0-9.-]+(?::[0-9]+)?|http://localhost(?::[0-9]+)?)$'
RuntimeAccessKeyAEnabled:
  Type: String
  Default: 'true'
  AllowedValues: ['true', 'false']
RuntimeAccessKeyBEnabled:
  Type: String
  Default: 'false'
  AllowedValues: ['true', 'false']
RuntimeActiveAccessKeySlot:
  Type: String
  Default: A
  AllowedValues: [A, B]
AutomatedAbrEnabled:
  Type: String
  Default: 'false'
  AllowedValues: ['true', 'false']
AccelerationMode:
  Type: String
  Default: DISABLED
  AllowedValues: [DISABLED, PREFERRED]
```

Set `MediaBucketName` through a condition: an empty value resolves to `mediacms-${AWS::AccountId}-us-east-1`; dev parameter files must override it. Add CloudFormation `Rules` asserting prod uses `ResourceNamePrefix=mediacms`, dev uses `ResourceNamePrefix=mediacms-dev`, at least one key slot is enabled, the active slot is enabled, and MVP feature parameters remain `AutomatedAbrEnabled=false` plus `AccelerationMode=DISABLED`. Add a byte-size test requiring the template to remain at or below the CloudFormation `TemplateBody` limit of 51,200 bytes. Add only the declared output names initially so the test becomes green as resources are added.

- [ ] **Step 4: Run tests and lint the skeleton**

Run:

```bash
.venv/bin/pytest tests/aws_infrastructure/test_core_template_contract.py -q
cfn-lint infra/aws/mediacms-core.yaml
```

Expected: PASS with no cfn-lint errors.

- [ ] **Step 5: Commit**

```bash
git add infra/aws/mediacms-core.yaml tests/aws_infrastructure
git commit -m "test: define aws infrastructure contract"
```

### Task 2: Private S3 and CloudFront Playback Edge

**Files:**
- Modify: `infra/aws/mediacms-core.yaml`
- Modify: `tests/aws_infrastructure/test_core_template_contract.py`

**Interfaces:**
- Produces S3 prefixes: `uploads/`, `originals/`, `candidates/`, `system/defaults/`.
- Produces a private `MediaBucket`, `MediaOAC`, two CloudFront Public Keys, one Key Group, a credentialed CORS Response Headers Policy, `MediaDistribution`, and source-ARN-bound `MediaBucketPolicy`.
- Default distribution uses the CloudFront certificate. Custom mode consumes an already-issued `AcmCertificateArn`; it does not create or wait for ACM.

- [ ] **Step 1: Write failing S3 and CloudFront semantic tests**

Assert observable template properties:

```python
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
    assert "VersioningConfiguration" not in props
    assert props["BucketEncryption"]["ServerSideEncryptionConfiguration"][0][
        "ServerSideEncryptionByDefault"
    ]["SSEAlgorithm"] == "AES256"
    rule = props["LifecycleConfiguration"]["Rules"][0]
    assert rule["AbortIncompleteMultipartUpload"]["DaysAfterInitiation"] == 1


def test_cloudfront_uses_oac_key_group_and_no_public_s3_origin():
    template = load_template(CORE)
    distribution = template["Resources"]["MediaDistribution"]["Properties"]["DistributionConfig"]
    assert distribution["DefaultCacheBehavior"]["TrustedKeyGroups"]
    assert distribution["Origins"][0]["OriginAccessControlId"]
    statements = template["Resources"]["MediaBucketPolicy"]["Properties"]["PolicyDocument"]["Statement"]
    assert statements == [statements[0]]
    assert statements[0]["Principal"] == {"Service": "cloudfront.amazonaws.com"}
    assert "AWS:SourceArn" in statements[0]["Condition"]["StringEquals"]
```

Also assert S3 upload CORS permits only `ApplicationOrigin`, `PUT` and `HEAD`, exposes `ETag` and checksum headers, and that custom-domain Rules require both a domain and issued certificate ARN when enabled.

- [ ] **Step 2: Run the focused tests and observe missing resources**

Run: `.venv/bin/pytest tests/aws_infrastructure/test_core_template_contract.py -q`

Expected: FAIL with missing `MediaBucket` or `MediaDistribution`.

- [ ] **Step 3: Implement private storage and playback resources**

Use BucketOwnerEnforced ownership, AES256 default encryption, public-access blocking and one-day incomplete Multipart cleanup. Do not enable S3 Versioning: immutable attempt/version keys provide isolation without retaining complete noncurrent video copies. Do not add public ACLs or public bucket statements.

CloudFront must have exactly one S3 origin, OAC `SigningBehavior=always`, `ViewerProtocolPolicy=redirect-to-https`, HTTP/2+3, IPv6, `PriceClass_100`, compressed caching, GET/HEAD/OPTIONS allowed methods, credentialed CORS response headers, and the Key Group in `TrustedKeyGroups`. The bucket policy grants only `s3:GetObject` to `cloudfront.amazonaws.com` under this distribution's `AWS:SourceArn`.

Custom domain logic:

```yaml
ViewerCertificate: !If
  - UseCustomDomain
  - AcmCertificateArn: !Ref AcmCertificateArn
    SslSupportMethod: sni-only
    MinimumProtocolVersion: TLSv1.2_2021
  - CloudFrontDefaultCertificate: true
Aliases: !If [UseCustomDomain, [!Ref MediaDomainName], !Ref AWS::NoValue]
```

- [ ] **Step 4: Run focused tests and cfn-lint**

Run:

```bash
.venv/bin/pytest tests/aws_infrastructure/test_core_template_contract.py -q
cfn-lint infra/aws/mediacms-core.yaml
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add infra/aws/mediacms-core.yaml tests/aws_infrastructure/test_core_template_contract.py
git commit -m "feat: add private media storage and cloudfront"
```

### Task 3: Runtime IAM User, A/B Credentials and MediaConvert Service Role

**Files:**
- Modify: `infra/aws/mediacms-core.yaml`
- Modify: `tests/aws_infrastructure/test_core_template_contract.py`

**Interfaces:**
- Produces: `MediaCMSRuntimeUser`, `MediaCMSRuntimePolicy`, conditional `RuntimeAccessKeyA/B`, `RuntimeCredentialsSecret`, and `MediaConvertServiceRole`.
- Runtime Secret JSON keys are exactly `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, and `AWS_DEFAULT_REGION`.
- Runtime policy must not grant `iam:Create*`, `iam:Delete*`, `cloudformation:*`, `secretsmanager:GetSecretValue`, or wildcard S3 access.

- [ ] **Step 1: Write failing least-privilege and secret-safety tests**

Add helpers that flatten IAM statements and assert:

```python
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


def test_runtime_policy_has_no_administrative_or_cross_bucket_permissions():
    statements = runtime_policy_statements(load_template(CORE))
    actions = {action for statement in statements for action in as_list(statement["Action"])}
    assert "cloudformation:*" not in actions
    assert "secretsmanager:GetSecretValue" not in actions
    assert not any(action.startswith("iam:Create") or action.startswith("iam:Delete") for action in actions)
    assert not any(statement.get("Resource") == "*" and "s3:GetObject" in as_list(statement["Action"]) for statement in statements)
```

Assert the MediaConvert role reads only `originals/*`, writes only `candidates/*`, lists only those prefixes, and the Runtime User can pass only this exact role with `iam:PassedToService=mediaconvert.amazonaws.com`.

- [ ] **Step 2: Run tests and observe missing IAM resources**

Run: `.venv/bin/pytest tests/aws_infrastructure/test_core_template_contract.py -q`

Expected: FAIL with missing runtime or MediaConvert IAM resources.

- [ ] **Step 3: Implement IAM and A/B credentials**

Create a named runtime user `mediacms-${Environment}-runtime` with no LoginProfile. Add conditional AccessKey resources and build the Secret using `Fn::If` on `RuntimeActiveAccessKeySlot`. Use `NoEcho` only for input parameters that are genuinely secret; public CloudFront keys are not secret.

Runtime S3 permissions:

- Bucket: `GetBucketLocation`, `ListBucket`, `ListBucketMultipartUploads`, restricted with `s3:prefix` to `uploads/*`, `originals/*`, `candidates/*`, and `system/defaults/*`.
- Objects: `GetObject`, `PutObject`, `DeleteObject`, `AbortMultipartUpload`, and `ListMultipartUploadParts` only beneath those four prefixes.
- MediaConvert: `CreateJob`, `GetJob`, `CancelJob`, `GetJobTemplate`, and `DescribeEndpoints`; require request tags `Project=mediacms` and matching `Environment` where AWS supports request-tag conditions.
- IAM: `PassRole` only on `MediaConvertServiceRole` and only to `mediaconvert.amazonaws.com`.

Explicitly deny the design from drifting back to custom monitoring: the Runtime User has no `cloudwatch:PutMetricData`, SNS, EventBridge or SQS permission.

Do not give the runtime user Secrets Manager read access; the administrator extracts the Secret using the deployment profile.

- [ ] **Step 4: Run tests, cfn-lint and IAM policy validation checks**

Run:

```bash
.venv/bin/pytest tests/aws_infrastructure/test_core_template_contract.py -q
cfn-lint infra/aws/mediacms-core.yaml
```

Expected: PASS. During real deployment, IAM Access Analyzer validation is an acceptance check, not a substitute for these tests.

- [ ] **Step 5: Commit**

```bash
git add infra/aws/mediacms-core.yaml tests/aws_infrastructure/test_core_template_contract.py
git commit -m "feat: add mediacms runtime aws identity"
```

### Task 4: Versioned MediaConvert Video and Audio Templates

**Files:**
- Modify: `infra/aws/mediacms-core.yaml`
- Create: `tests/aws_infrastructure/test_mediaconvert_templates.py`

**Interfaces:**
- Produces CloudFormation resources `VideoHlsJobTemplate` and `AudioHlsJobTemplate`.
- Produces template names `${ResourceNamePrefix}-video-hls-v1` and `${ResourceNamePrefix}-audio-hls-v1`; production/dev Rules make these exact and collision-free.
- Inputs and output destinations remain overrideable at CreateJob time; no real S3 object path is hardcoded into templates.

- [ ] **Step 1: Write failing MediaConvert settings tests**

Parse each resource's `SettingsJson` string with `json.loads`. Assert the video input selector uses `Rotate=AUTO`, the HLS group uses 4-second segments, and each video output has literal dimensions/QVBR settings:

```python
EXPECTED_VIDEO_OUTPUTS = {
    (1920, 1080): (8, 6_000_000),
    (1280, 720): (8, 4_000_000),
    (854, 480): (7, 1_000_000),
    (640, 360): (7, 700_000),
}


def test_video_template_uses_fixed_qvbr_ladder_and_auto_rotation():
    settings = settings_json("VideoHlsJobTemplate")
    selector = settings["Inputs"][0]["VideoSelector"]
    assert selector["Rotate"] == "AUTO"
    outputs = video_outputs(settings)
    actual = {
        (output["VideoDescription"]["Width"], output["VideoDescription"]["Height"]): (
            output["VideoDescription"]["CodecSettings"]["H264Settings"]["QvbrSettings"]["QvbrQualityLevel"],
            output["VideoDescription"]["CodecSettings"]["H264Settings"]["MaxBitrate"],
        )
        for output in outputs
    }
    assert actual == EXPECTED_VIDEO_OUTPUTS
```

For every H.264 output assert `RateControlMode=QVBR`, `QualityTuningLevel=SINGLE_PASS_HQ`, no `MaxAverageBitrate`, even dimensions, AAC audio, and no Automated ABR. Assert template acceleration is `DISABLED`. Assert the audio template has no `VideoDescription` and contains one AAC HLS output.

- [ ] **Step 2: Run tests and observe missing Job Templates**

Run: `.venv/bin/pytest tests/aws_infrastructure/test_mediaconvert_templates.py -q`

Expected: FAIL because the Job Template resources are absent.

- [ ] **Step 3: Implement both Job Templates**

Use `AWS::MediaConvert::JobTemplate` with `!Sub '${ResourceNamePrefix}-video-hls-v1'` and `!Sub '${ResourceNamePrefix}-audio-hls-v1'`, standard tags and JSON settings. `AWS::MediaConvert::JobTemplate` has no `Status` CloudFormation property. Omit `StatusUpdateInterval` because the API-only reconciler does not consume EventBridge status updates. The video template contains one Apple HLS output group with four H.264/AAC variants plus a frame-capture file output group. Frame capture is supplementary, never the only output. The audio template contains one Apple HLS AAC output and no fake video.

The static template contains all four video renditions. Document in the resource description that the processing coordinator must remove renditions above probed source resolution before `CreateJob`; the template itself cannot make that per-input decision.

- [ ] **Step 4: Run tests and CloudFormation lint**

Run:

```bash
.venv/bin/pytest tests/aws_infrastructure/test_mediaconvert_templates.py -q
cfn-lint infra/aws/mediacms-core.yaml
```

Expected: PASS. Do not submit a MediaConvert Job in this task.

- [ ] **Step 5: Commit**

```bash
git add infra/aws/mediacms-core.yaml tests/aws_infrastructure/test_mediaconvert_templates.py
git commit -m "feat: add qvbr mediaconvert templates"
```

### Task 5: Remove AWS Alerting and Minimize Persistent Cost

**Files:**
- Modify: `infra/aws/mediacms-core.yaml`
- Modify: `tests/aws_infrastructure/test_core_template_contract.py`
- Replace: `tests/aws_infrastructure/test_cloudwatch_contract.py`
- Modify: `docs/superpowers/specs/aws-backend-integration/02-aws-infrastructure-and-storage.md`
- Modify: `docs/superpowers/specs/aws-backend-integration/04-media-processing-orchestration.md`
- Modify: `docs/superpowers/specs/aws-backend-integration/07-deployment-and-acceptance.md`
- Modify: `docs/superpowers/specs/aws-backend-integration/10-test-and-deployment-plan.md`

**Interfaces:**
- Produces a core Stack with no SNS, CloudWatch alarm/dashboard/custom metric, EventBridge, SQS or monitoring Lambda resource.
- Removes `AlarmNotificationEmail`, S3 Versioning and Runtime `cloudwatch:PutMetricData` permission.
- Defers abnormal-job detection to the processing reconciler contract in `2026-08-02-api-polling-cost-minimization-design.md`; this infrastructure task does not implement the reconciler.

- [ ] **Step 1: Replace positive monitoring tests with failing absence and cost-boundary tests**

```python
FORBIDDEN_RESOURCE_PREFIXES = (
    "AWS::SNS::",
    "AWS::CloudWatch::",
    "AWS::Events::",
    "AWS::SQS::",
)


def test_core_stack_has_no_aws_alerting_or_custom_monitoring_resources():
    template = load_template(CORE)
    resource_types = [resource["Type"] for resource in template["Resources"].values()]
    assert not any(
        resource_type.startswith(FORBIDDEN_RESOURCE_PREFIXES)
        for resource_type in resource_types
    )
    assert "AlarmNotificationEmail" not in template["Parameters"]


def test_runtime_has_no_cloudwatch_metric_write_permission():
    statements = runtime_policy_statements(load_template(CORE))
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
```

- [ ] **Step 2: Run focused tests and verify they fail against the previously implemented resources**

Run:

```bash
.venv/bin/pytest \
  tests/aws_infrastructure/test_cloudwatch_contract.py \
  tests/aws_infrastructure/test_core_template_contract.py \
  -q
```

Expected: FAIL because monitoring resources, the email parameter, S3 Versioning and `cloudwatch:PutMetricData` still exist.

- [ ] **Step 3: Remove resources, parameter, condition and permission**

Delete `AlarmNotificationEmail`, `HasAlarmNotificationEmail`, `MediaInfrastructureAlerts`, `MediaInfrastructureEmailSubscription`, all six alarms and `MediaInfrastructureDashboard`. Delete only the `PublishProcessingMetrics` IAM statement. Remove `VersioningConfiguration` while preserving bucket retention, encryption, Block Public Access and one-day incomplete multipart cleanup.

- [ ] **Step 4: Update modular design and acceptance documents**

Replace CloudWatch/SNS promises with the exact MediaConvert `GetJob` reconciliation boundary: five provider states, adaptive polling, deduplicated in-site warnings, sanitized errors and no automatic black/padding detection. Remove CloudWatch capability checks from deployment and credential rotation; retain S3 and MediaConvert positive/negative checks.

- [ ] **Step 5: Run infrastructure tests and lint**

Run:

```bash
.venv/bin/pytest tests/aws_infrastructure -q
cfn-lint infra/aws/mediacms-core.yaml infra/aws/mediacms-certificate.yaml
git diff --check
```

Expected: PASS. The core template remains below 51,200 bytes and contains no forbidden monitoring resources.

- [ ] **Step 6: Commit**

```bash
git add infra/aws/mediacms-core.yaml tests/aws_infrastructure docs/superpowers/specs/aws-backend-integration
git commit -m "refactor: replace aws alerts with api reconciliation"
```

### Task 6: Optional ACM Certificate Stack and Cloudflare Gate

**Files:**
- Create: `infra/aws/mediacms-certificate.yaml`
- Create: `tests/aws_infrastructure/test_certificate_template.py`
- Create: `deploy/scripts/describe_acm_validation.sh`
- Create: `deploy/scripts/test_aws_infrastructure_scripts.sh`

**Interfaces:**
- Certificate Stack consumes `Environment` and `MediaDomainName`; produces only `CertificateArn`.
- `describe_acm_validation.sh STACK_NAME` calls `cloudformation describe-stack-resource --logical-resource-id MediaCertificate` so it works while the Stack is still `CREATE_IN_PROGRESS`, then calls ACM `describe-certificate` and prints only DNS validation name/type/value/status.
- This task validates but does not deploy the certificate Stack while the Cloudflare gate is closed.

- [ ] **Step 1: Write failing certificate and script tests**

Assert the certificate template contains one DNS-validated `AWS::CertificateManager::Certificate`, RSA 2048, transparency logging enabled, tags, and no Route53 resources. Shell tests use a fake `aws` executable to verify every invocation includes `--profile default --region us-east-1` and that the script never calls create/update APIs outside CloudFormation.

- [ ] **Step 2: Run tests and observe missing files**

Run:

```bash
.venv/bin/pytest tests/aws_infrastructure/test_certificate_template.py -q
bash deploy/scripts/test_aws_infrastructure_scripts.sh
```

Expected: FAIL because the certificate template/script is absent.

- [ ] **Step 3: Implement certificate template and read-only validation helper**

The certificate Stack must not create Route53 records because DNS is managed by Cloudflare. The helper resolves the certificate ARN through `cloudformation describe-stack-resource --stack-name "$stack_name" --logical-resource-id MediaCertificate`, then requests validation data through `acm describe-certificate`. It must fail closed if the Stack resource or validation record is unavailable and must not print private keys or unrelated Stack parameters.

- [ ] **Step 4: Run pytest, shell tests, shellcheck, shfmt check and cfn-lint**

Run:

```bash
.venv/bin/pytest tests/aws_infrastructure/test_certificate_template.py -q
bash deploy/scripts/test_aws_infrastructure_scripts.sh
shellcheck deploy/scripts/describe_acm_validation.sh deploy/scripts/test_aws_infrastructure_scripts.sh
shfmt -d deploy/scripts/describe_acm_validation.sh deploy/scripts/test_aws_infrastructure_scripts.sh
cfn-lint infra/aws/mediacms-certificate.yaml
```

Expected: PASS. Do not create the ACM Stack yet.

- [ ] **Step 5: Commit**

```bash
git add infra/aws/mediacms-certificate.yaml tests/aws_infrastructure deploy/scripts
git commit -m "feat: add optional cloudflare acm gate"
```

### Task 7: Validation, Change Set and Secret Extraction Scripts

**Files:**
- Create: `deploy/scripts/validate_aws_infrastructure.sh`
- Create: `deploy/scripts/create_aws_change_set.sh`
- Create: `deploy/scripts/describe_aws_change_set.sh`
- Create: `deploy/scripts/extract_runtime_aws_env.sh`
- Create: `infra/aws/README.md`
- Modify: `deploy/scripts/test_aws_infrastructure_scripts.sh`

**Interfaces:**
- `validate_aws_infrastructure.sh` runs local cfn-lint, then AWS `validate-template` using `default/us-east-1`.
- `create_aws_change_set.sh STACK_NAME CHANGE_SET_NAME PARAMETER_FILE` creates `CREATE` for an absent Stack and `UPDATE` otherwise, always with `CAPABILITY_NAMED_IAM`, and never executes it.
- `describe_aws_change_set.sh STACK_NAME CHANGE_SET_NAME` prints resource action/logical ID/type/replacement and IAM capability without parameter values.
- `extract_runtime_aws_env.sh STACK_NAME OUTPUT_PATH` reads the Secret with the administrator profile and writes exactly four approved variables using a temporary file, `umask 0077`, atomic rename and final mode `0640`; it never prints values.

- [ ] **Step 1: Extend failing fake-AWS script tests**

Test CREATE and UPDATE selection, mandatory profile/region/capability flags, no `execute-change-set`, sanitized describe output, and atomic Secret extraction. The fake Secret response uses literal non-production values, and tests assert they appear only in the output fixture—not stdout/stderr.

- [ ] **Step 2: Run shell tests and observe missing scripts**

Run: `bash deploy/scripts/test_aws_infrastructure_scripts.sh`

Expected: FAIL because the deployment scripts do not exist.

- [ ] **Step 3: Implement scripts and operator documentation**

All scripts start with `#!/usr/bin/env bash` and `set -euo pipefail`, validate positional arguments, quote paths, and use explicit Stack names. The extraction script accepts a required Unix group argument and installs the file with `install -o root -g "$runtime_group" -m 0640`; it must require sudo only for the final installation and clean temporary files via `trap`.

Document the initial A slot and exact rotation sequence:

1. Change Set: A=true, B=true, Active=A.
2. Change Set: A=true, B=true, Active=B; extract env, restart Web/Worker, verify S3 and MediaConvert API access, observe.
3. Change Set: A=false, B=true, Active=B.
4. Reverse A/B for the next rotation.

Document that an AccessKey is a long-term secret, how to disable the active key through a CloudFormation Change Set during an incident, and that Stack deletion/retained resource cleanup needs separate approval.

- [ ] **Step 4: Run all script quality gates**

Run:

```bash
bash deploy/scripts/test_aws_infrastructure_scripts.sh
shellcheck deploy/scripts/validate_aws_infrastructure.sh deploy/scripts/create_aws_change_set.sh deploy/scripts/describe_aws_change_set.sh deploy/scripts/extract_runtime_aws_env.sh deploy/scripts/describe_acm_validation.sh deploy/scripts/test_aws_infrastructure_scripts.sh
shfmt -d deploy/scripts/validate_aws_infrastructure.sh deploy/scripts/create_aws_change_set.sh deploy/scripts/describe_aws_change_set.sh deploy/scripts/extract_runtime_aws_env.sh deploy/scripts/describe_acm_validation.sh deploy/scripts/test_aws_infrastructure_scripts.sh
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add deploy/scripts infra/aws/README.md
git commit -m "feat: add cloudformation deployment gates"
```

### Task 8: Offline and AWS-Side Template Validation

**Files:**
- Modify: `tests/aws_infrastructure/test_core_template_contract.py`
- Modify: `infra/aws/README.md`

**Interfaces:**
- Consumes templates/scripts from Tasks 1–7.
- Produces validation evidence only; creates no Stack and no billable resource.

- [ ] **Step 1: Run all offline infrastructure tests**

Run:

```bash
.venv/bin/pytest tests/aws_infrastructure -q
cfn-lint infra/aws/mediacms-core.yaml infra/aws/mediacms-certificate.yaml
bash deploy/scripts/test_aws_infrastructure_scripts.sh
shellcheck deploy/scripts/*.sh
shfmt -d deploy/scripts/*.sh
git diff --check
```

Expected: zero failures/errors.

- [ ] **Step 2: Validate both templates against AWS**

Run:

```bash
aws --profile default --region us-east-1 cloudformation validate-template --template-body file://infra/aws/mediacms-core.yaml
aws --profile default --region us-east-1 cloudformation validate-template --template-body file://infra/aws/mediacms-certificate.yaml
```

Expected: both return template descriptions/parameters with no validation error. This is read-only and creates no resources.

- [ ] **Step 3: Verify account/region and bucket-name availability read-only**

Run:

```bash
aws --profile default --region us-east-1 sts get-caller-identity
aws --profile default --region us-east-1 s3api head-bucket --bucket "mediacms-$(aws --profile default --region us-east-1 sts get-caller-identity --query Account --output text)-us-east-1-dev"
```

Expected: identity matches the intended account. For the proposed new dev bucket, `head-bucket` should report not found/404; 200 means choose a different explicit override before creating a Change Set. A 403 is ambiguous and must stop deployment for manual ownership review.

- [ ] **Step 4: Record only non-secret validation conclusions in README**

Record tool versions, validated region, template hashes, and whether the bucket override is available. Do not record account ID, public-key material, Secret ARN, credentials, domains not yet approved, or command output containing identity details.

- [ ] **Step 5: Commit**

```bash
git add tests/aws_infrastructure infra/aws/README.md
git commit -m "test: validate aws infrastructure templates"
```

### Task 9: Dev Change Set Review and Explicit Execution Gate

**Files:**
- Create locally but do not commit: `/tmp/mediacms-dev-parameters.json`
- Modify after successful deployment: `infra/aws/README.md`

**Interfaces:**
- Produces a reviewed UTC-timestamped Change Set whose name begins `mediacms-dev-initial-` for Stack `mediacms-dev`.
- This task stops after Change Set review. Executing the Change Set requires a new explicit administrator confirmation because it creates billable AWS resources and a long-term AccessKey.

- [ ] **Step 1: Generate two CloudFront RSA-2048 key pairs locally**

Use `umask 0077` and write private/public files outside the repository under `/etc/mediacms/secrets/cloudfront/` or a protected temporary directory. Never send private keys to CloudFormation. Public PEM values are inserted into the uncommitted parameter file; private keys are retained for the later playback-signing plan.

- [ ] **Step 2: Build the uncommitted dev parameter file**

Use literal values:

- `Environment=dev`
- `ResourceNamePrefix=mediacms-dev`
- `MediaBucketName` is constructed as `mediacms-${verified_account_id}-us-east-1-dev`, where `verified_account_id` is read from the approved `sts get-caller-identity` result.
- `ApplicationOrigin` is the exact approved dev browser origin. `http://localhost:3000` illustrates the permitted local Arch browser form; any non-local origin must use HTTPS.
- A enabled, B disabled, Active A
- Automated ABR false, Acceleration DISABLED
- custom domain false, domain/ACM ARN empty
- both CloudFront public PEM keys

If the approved dev application hostname is not yet available, stop here; do not invent or hardcode a placeholder origin.

- [ ] **Step 3: Create but do not execute the Change Set**

Run:

```bash
deploy/scripts/create_aws_change_set.sh mediacms-dev "mediacms-dev-initial-$(date -u +%Y%m%dT%H%M%SZ)" /tmp/mediacms-dev-parameters.json
```

Expected: Change Set reaches `CREATE_COMPLETE` and remains unexecuted.

- [ ] **Step 4: Review exact changes and security scope**

Run the sanitized describe script. Confirm only MediaCMS resources are created, IAM capability is declared, bucket name is the verified dev override, no Route53/Vercel/IAM LoginProfile appears, and no old `media-platform` ARN/name is referenced.

- [ ] **Step 5: Stop for explicit administrator approval**

Report the Change Set name, resource-type/action summary, estimated cost-bearing resources (CloudFront usage, MediaConvert job usage, Secrets Manager and S3 storage), and that execution creates a long-term IAM AccessKey. Explicitly confirm that no SNS, CloudWatch alarm/dashboard/custom metric, EventBridge or SQS resource is present. Do not run `execute-change-set` until the administrator explicitly approves this exact Change Set.

### Task 10: Execute and Verify the Dev Stack After Approval

**Files:**
- Modify: `infra/aws/README.md`
- Modify: `docs/superpowers/plans/2026-08-02-aws-integration-roadmap.md`

**Interfaces:**
- Consumes the exact approved Change Set from Task 9.
- Produces a deployed `mediacms-dev` Stack and non-secret capability evidence for Browser Ingestion and Processing Orchestration plans.

- [ ] **Step 1: Execute only the approved Change Set and wait**

Run:

```bash
test -n "${APPROVED_CHANGE_SET_NAME:?Set this to the exact administrator-approved Change Set name}"
aws --profile default --region us-east-1 cloudformation execute-change-set --stack-name mediacms-dev --change-set-name "$APPROVED_CHANGE_SET_NAME"
aws --profile default --region us-east-1 cloudformation wait stack-create-complete --stack-name mediacms-dev
```

The concrete Change Set name must be copied from the approval; never use a wildcard or “latest” lookup.

- [ ] **Step 2: Verify non-secret Stack outputs and resource configuration**

Use read-only `describe-stacks`, `get-template`, S3 encryption/public-access/CORS/lifecycle calls, CloudFront distribution/OAC/key-group calls, MediaConvert `get-job-template`, and IAM policy reads. Confirm S3 Versioning is not enabled and no monitoring resources exist. Do not print Secret values.

- [ ] **Step 3: Extract dev runtime credentials safely and run positive capability checks**

Extract to a protected dev-only env file. In a disposable shell that sources that file, verify bucket location/list for permitted prefixes, a small tagged object PUT/HEAD/DELETE beneath an exact dev test prefix, and MediaConvert template reads. Delete only the exact test object key created by this step. Do not test CloudWatch custom metrics because the runtime has no CloudWatch write permission.

- [ ] **Step 4: Run negative least-privilege checks**

Using the runtime credential, verify denied access to an unrelated bucket/key, IAM management, CloudFormation listing, Secrets Manager Secret retrieval, and passing any role other than the exact MediaConvert service role. A denial is expected; an unexpected allow is a release blocker.

- [ ] **Step 5: Verify CloudFront private origin behavior**

Assert direct S3 anonymous GET is denied. Assert unsigned CloudFront GET to a protected test key is denied because the behavior trusts the Key Group. Signed-cookie playback is deferred to the CloudFront playback plan, but OAC origin access and private viewer enforcement must be observable.

- [ ] **Step 6: Update roadmap status and commit evidence summary**

Mark only `AWS infrastructure` complete after all positive/negative checks pass. Record Stack name, template hash, resource IDs needed by later plans and test conclusions without account IDs, Secret values or key material.

```bash
git add infra/aws/README.md docs/superpowers/plans/2026-08-02-aws-integration-roadmap.md
git commit -m "feat: provision mediacms dev aws foundation"
```

## Plan Completion Gate

The AWS infrastructure plan is complete only when:

- Core and certificate templates pass pytest semantic contracts, cfn-lint and AWS validate-template.
- The reviewed dev Change Set contains only independent `mediacms-*` resources and is explicitly approved before execution.
- The private bucket has encryption, Block Public Access, constrained CORS and Multipart cleanup, with S3 Versioning intentionally absent.
- CloudFront uses OAC, an S3 source-ARN bucket policy and a trusted Key Group; S3 and unsigned viewer access are denied.
- Runtime A/B credential rules reject invalid slot combinations, the Secret is retained, and no secret appears in Outputs/logs/repository.
- Runtime credentials pass permitted S3/MediaConvert operations and fail cross-project, IAM, CloudFormation, Secrets Manager and CloudWatch metric-write operations.
- Video/audio Job Templates match the fixed HLS/QVBR/AUTO-rotation contract with Automated ABR and acceleration disabled.
- No SNS, CloudWatch alarm/dashboard/custom metric, EventBridge, SQS or monitoring Lambda resource exists; abnormal-job alerts are owned by the later `GetJob` reconciler implementation.
- Cloudflare/ACM custom-domain resources remain undeployed until the external DNS gate is explicitly opened.
- No existing `/home/caoyujie/projects/cyj/media-platform` AWS resource is modified or deleted.
