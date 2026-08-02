# MediaCMS AWS Infrastructure

The templates create only the required private S3, IAM, Secrets Manager, MediaConvert and CloudFront resources in `us-east-1`. They do not create SNS, CloudWatch alarms or dashboards, custom metrics, EventBridge, SQS, monitoring Lambda resources or S3 Versioning.

The Runtime policy grants `mediaconvert:Probe` for validated project originals and
`mediaconvert:ListJobs` for recovery after an unknown CreateJob result. AWS does not
support resource-level scoping for these two actions, so they share one explicit
`Resource: '*'` statement. `GetJob` and `CancelJob` remain scoped to Job ARNs;
MediaConvert `CreateJob`, S3 and `iam:PassRole` retain their existing tag, prefix and
exact-role restrictions.

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

## Validation evidence

Validated on 2026-08-02 in `us-east-1` without creating a Stack:

- AWS CLI `2.32.6`; `cfn-lint 1.53.3`; pytest `9.1.1`; PyYAML `6.0.3`.
- Both templates passed local `cfn-lint` and AWS CloudFormation `validate-template` using profile `default`.
- The intended AWS account was confirmed without recording its account ID.
- The account-derived dev bucket override returned an unambiguous 404 and was available at validation time.
- Core template SHA-256: `dbd66d308e3afcd3b55b854a4ee633323773a074b6e991951d63719730e1e72a`.
- Certificate template SHA-256: `3e0a93bdbc47a35a9a07cbebd3565a161775638a16622ce5fdfc1677aec83973`.

Bucket availability is time-sensitive and must be checked again immediately before creating the initial Change Set.

## Dev deployment evidence

The `mediacms-dev` Stack reached `CREATE_COMPLETE` on 2026-08-02 after the exact reviewed Change Set `mediacms-dev-retry-20260802T025044Z` was approved. Validation recorded only non-secret conclusions:

- All 15 expected S3, IAM, Secrets Manager, MediaConvert and CloudFront resources reached `CREATE_COMPLETE`.
- The Bucket uses SSE-S3 (`AES256`), Bucket-owner-enforced ownership, all four Block Public Access controls, the approved upload CORS contract and one-day incomplete multipart cleanup. S3 Versioning remains disabled.
- The CloudFront Distribution is deployed with OAC and one trusted Key Group. Anonymous direct S3 access and unsigned CloudFront access both returned `403`.
- The runtime identity can use only the approved media prefixes, read both MediaConvert Job Templates and discover the MediaConvert endpoint. The exact verification object was deleted and no verification object remains.
- Runtime requests for an unauthorized key, unrelated Bucket, IAM management, CloudFormation listing, Secrets Manager retrieval and CloudWatch metric writes were denied.
- IAM policy simulation allows `iam:PassRole` only for the Stack's exact MediaConvert service Role; a different Role is implicitly denied.
- The reviewed Change Set `mediacms-dev-mediaconvert-reconcile-20260802T055022Z`
  updated only `MediaCMSRuntimePolicy` without replacement. The restricted Runtime
  identity successfully called `ListJobs` and probed a disposable project
  `originals/` video, while CloudFormation access and a foreign S3 Probe input
  remained denied. The verification object and isolated prefix were empty after
  cleanup.
- The Stack contains no SNS, CloudWatch alarm/dashboard, custom metric, EventBridge, SQS or monitoring Lambda resource.
- The protected dev runtime env is installed outside the repository with owner `root`, group `caoyujie` and mode `0640`; its values were not printed or committed.

The optional ACM/custom-domain Stack remains undeployed until the Cloudflare DNS gate is opened.

## Browser ingestion acceptance evidence

Validated on 2026-08-02 against PostgreSQL 17 and the deployed `mediacms-dev` Stack:

- Browser ingestion/domain tests passed with strict FIFO upload leases, resumable file uploads, browser-expanded HLS validation and administrator-only API coverage.
- A real SigV4 presigned Multipart Part was uploaded beneath an isolated `uploads/verification/browser-ingestion-{uuid}/` prefix and reconciled through `ListParts`.
- The exact Multipart upload was aborted after verification; no object or incomplete Multipart upload remained.
- The live check identified and fixed the `us-east-1` legacy presigning fallback by explicitly requiring Signature Version 4.
- `deploy/scripts/smoke_browser_upload.py` reproduces the non-destructive check using the `default` profile and always attempts exact cleanup in `finally`.
