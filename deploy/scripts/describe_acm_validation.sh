#!/usr/bin/env bash
set -euo pipefail

if (($# != 1)); then
	printf 'Usage: %s STACK_NAME\n' "${0##*/}" >&2
	exit 64
fi

stack_name=$1
if [[ -z "$stack_name" ]]; then
	printf '%s\n' 'STACK_NAME must not be empty' >&2
	exit 64
fi

certificate_arn=$(
	aws --profile default --region us-east-1 \
		cloudformation describe-stack-resource \
		--stack-name "$stack_name" \
		--logical-resource-id MediaCertificate \
		--query 'StackResourceDetail.PhysicalResourceId' \
		--output text
)

if [[ -z "$certificate_arn" || "$certificate_arn" == "None" ]]; then
	printf '%s\n' 'MediaCertificate is not yet available in the Stack' >&2
	exit 1
fi

validation_record=$(
	aws --profile default --region us-east-1 \
		acm describe-certificate \
		--certificate-arn "$certificate_arn" \
		--query 'Certificate.DomainValidationOptions[0].[ResourceRecord.Name,ResourceRecord.Type,ResourceRecord.Value,ValidationStatus]' \
		--output text
)

if [[ -z "$validation_record" || "$validation_record" == *"None"* ]]; then
	printf '%s\n' 'ACM DNS validation record is not yet available' >&2
	exit 1
fi

printf '%s\n' "$validation_record"
