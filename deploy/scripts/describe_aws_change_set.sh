#!/usr/bin/env bash
set -euo pipefail

if (($# != 2)); then
	printf 'Usage: %s STACK_NAME CHANGE_SET_NAME\n' "${0##*/}" >&2
	exit 64
fi

stack_name=$1
change_set_name=$2
if [[ -z "$stack_name" || -z "$change_set_name" ]]; then
	printf '%s\n' 'Stack name and Change Set name must not be empty' >&2
	exit 64
fi

printf '%s\n' 'Action LogicalResourceId ResourceType Replacement'
aws --profile default --region us-east-1 \
	cloudformation describe-change-set \
	--stack-name "$stack_name" \
	--change-set-name "$change_set_name" \
	--query 'Changes[].ResourceChange.[Action,LogicalResourceId,ResourceType,Replacement]' \
	--output text

printf '%s\n' 'Capabilities'
aws --profile default --region us-east-1 \
	cloudformation describe-change-set \
	--stack-name "$stack_name" \
	--change-set-name "$change_set_name" \
	--query 'Capabilities' \
	--output text
