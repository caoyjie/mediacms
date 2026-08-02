#!/usr/bin/env bash
set -euo pipefail

if (($# != 3)); then
	printf 'Usage: %s STACK_NAME CHANGE_SET_NAME PARAMETER_FILE\n' "${0##*/}" >&2
	exit 64
fi

stack_name=$1
change_set_name=$2
parameter_file=$3
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
template="$repo_root/infra/aws/mediacms-core.yaml"

if [[ -z "$stack_name" || -z "$change_set_name" || ! -f "$parameter_file" ]]; then
	printf '%s\n' 'Stack name, Change Set name and an existing parameter file are required' >&2
	exit 64
fi

if aws --profile default --region us-east-1 \
	cloudformation describe-stacks --stack-name "$stack_name" >/dev/null 2>&1; then
	change_set_type=UPDATE
else
	change_set_type=CREATE
fi

aws --profile default --region us-east-1 \
	cloudformation create-change-set \
	--stack-name "$stack_name" \
	--change-set-name "$change_set_name" \
	--change-set-type "$change_set_type" \
	--template-body "file://$template" \
	--parameters "file://$parameter_file" \
	--capabilities CAPABILITY_NAMED_IAM >/dev/null

aws --profile default --region us-east-1 \
	cloudformation wait change-set-create-complete \
	--stack-name "$stack_name" \
	--change-set-name "$change_set_name"

printf 'Change Set %s is ready for review and has not been executed.\n' "$change_set_name"
