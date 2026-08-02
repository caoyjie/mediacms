#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
core_template="$repo_root/infra/aws/mediacms-core.yaml"
certificate_template="$repo_root/infra/aws/mediacms-certificate.yaml"

cfn-lint "$core_template" "$certificate_template"
aws --profile default --region us-east-1 \
	cloudformation validate-template \
	--template-body "file://$core_template" >/dev/null
aws --profile default --region us-east-1 \
	cloudformation validate-template \
	--template-body "file://$certificate_template" >/dev/null

printf '%s\n' 'AWS infrastructure templates validated'
