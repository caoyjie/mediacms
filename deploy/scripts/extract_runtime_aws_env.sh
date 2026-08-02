#!/usr/bin/env bash
set -euo pipefail

if (($# != 3)); then
	printf 'Usage: %s STACK_NAME OUTPUT_PATH RUNTIME_GROUP\n' "${0##*/}" >&2
	exit 64
fi

stack_name=$1
output_path=$2
runtime_group=$3
if [[ -z "$stack_name" || -z "$output_path" || -z "$runtime_group" ]]; then
	printf '%s\n' 'Stack name, output path and runtime group must not be empty' >&2
	exit 64
fi

umask 0077
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT
secret_json="$work_dir/secret.json"
rendered_env="$work_dir/aws-runtime.env"

# Backticks below are JMESPath literals, not shell syntax.
# shellcheck disable=SC2016
secret_arn=$(
	aws --profile default --region us-east-1 \
		cloudformation describe-stacks \
		--stack-name "$stack_name" \
		--query 'Stacks[0].Outputs[?OutputKey==`RuntimeCredentialsSecretArn`].OutputValue | [0]' \
		--output text
)
if [[ -z "$secret_arn" || "$secret_arn" == "None" ]]; then
	printf '%s\n' 'RuntimeCredentialsSecretArn Stack output is unavailable' >&2
	exit 1
fi

aws --profile default --region us-east-1 \
	secretsmanager get-secret-value \
	--secret-id "$secret_arn" \
	--query 'SecretString' \
	--output text >"$secret_json"

python3 - "$secret_json" "$rendered_env" <<'PY'
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
approved = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
)
payload = json.loads(source.read_text(encoding="utf-8"))
if set(payload) != set(approved):
    raise SystemExit("runtime Secret contains unexpected keys")
lines = []
for key in approved:
    value = payload[key]
    if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
        raise SystemExit(f"invalid value for {key}")
    lines.append(f"{key}={value}")
destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

sudo bash -c '
set -euo pipefail
source_file=$1
destination=$2
runtime_group=$3
staged="${destination}.tmp.$$"
trap '\''rm -f "$staged"'\'' EXIT
install -o root -g "$runtime_group" -m 0640 "$source_file" "$staged"
mv -f "$staged" "$destination"
trap - EXIT
' _ "$rendered_env" "$output_path" "$runtime_group"

printf 'Runtime AWS environment installed at %s; secret values were not printed.\n' "$output_path"
