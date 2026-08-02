# MediaCMS AWS Infrastructure

The templates create only the required private S3, IAM, Secrets Manager, MediaConvert and CloudFront resources in `us-east-1`. They do not create SNS, CloudWatch alarms or dashboards, custom metrics, EventBridge, SQS, monitoring Lambda resources or S3 Versioning.

All AWS commands use the `default` profile. Validate before creating a Change Set:

```bash
deploy/scripts/validate_aws_infrastructure.sh
```

Create and inspect a Change Set without executing it:

```bash
deploy/scripts/create_aws_change_set.sh STACK_NAME CHANGE_SET_NAME PARAMETER_FILE
deploy/scripts/describe_aws_change_set.sh STACK_NAME CHANGE_SET_NAME
```

Execution requires explicit approval of that exact Change Set name. Never select a wildcard or the latest Change Set automatically.

## Runtime credentials

The Stack starts with slot A enabled, slot B disabled and slot A active. Extract the active credentials only to a protected host file:

```bash
deploy/scripts/extract_runtime_aws_env.sh STACK_NAME /etc/mediacms/secrets/aws-runtime.env mediacms
```

The command installs exactly four variables with owner `root`, group `mediacms` and mode `0640`. It never prints credential values. The administrator `default` profile is not copied into application containers.

Rotate credentials using three reviewed Change Sets:

1. Set A=true, B=true, Active=A.
2. Set A=true, B=true, Active=B; extract the env file, restart Web/Worker, verify S3 and MediaConvert API access, then observe.
3. Set A=false, B=true, Active=B.
4. Reverse A/B for the next rotation.

An AccessKey is a long-term secret. During an incident, use a CloudFormation Change Set to enable the inactive slot, switch the active slot, deploy and verify the new env, then disable the compromised slot. Do not call ad-hoc IAM create/delete APIs.

Stack deletion, retained Bucket/Secret cleanup, disabling the only active key and cleanup of old project AWS resources are destructive actions requiring separate approval.

## Optional custom media domain

The certificate template is not deployed until a real media hostname is approved. Cloudflare owns DNS; the template creates no Route53 record. After creating the certificate Stack through a reviewed Change Set, obtain only its validation record with:

```bash
deploy/scripts/describe_acm_validation.sh CERTIFICATE_STACK_NAME
```
