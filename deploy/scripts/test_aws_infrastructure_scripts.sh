#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

mkdir -p "$test_root/bin"
aws_log="$test_root/aws.log"
sudo_log="$test_root/sudo.log"
export AWS_TEST_LOG="$aws_log"
export SUDO_TEST_LOG="$sudo_log"

cat >"$test_root/bin/aws" <<'FAKE_AWS'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$AWS_TEST_LOG"

case "$*" in
*"cloudformation describe-stack-resource"*)
	printf '%s\n' 'arn:aws:acm:us-east-1:111122223333:certificate/00000000-0000-0000-0000-000000000000'
	;;
*"acm describe-certificate"*)
	printf '%s\n' '_validation.example.com. CNAME _token.acm-validations.aws. PENDING_VALIDATION'
	;;
*"cloudformation validate-template"*)
	printf '%s\n' '{"Description":"valid test template"}'
	;;
*"cloudformation describe-stacks"*"RuntimeCredentialsSecretArn"*)
	printf '%s\n' 'arn:aws:secretsmanager:us-east-1:111122223333:secret:mediacms-dev-runtime'
	;;
*"cloudformation describe-stacks"*)
	if [[ "${FAKE_STACK_EXISTS:-false}" == "true" ]]; then
		printf '%s\n' 'CREATE_COMPLETE'
	else
		exit 255
	fi
	;;
*"cloudformation create-change-set"*)
	printf '%s\n' 'arn:aws:cloudformation:us-east-1:111122223333:changeSet/test/0000'
	;;
*"cloudformation wait change-set-create-complete"*)
	;;
*"cloudformation describe-change-set"*"Changes"*)
	printf '%s\n' 'CREATE MediaBucket AWS::S3::Bucket Never'
	;;
*"cloudformation describe-change-set"*"Capabilities"*)
	printf '%s\n' 'CAPABILITY_NAMED_IAM'
	;;
*"secretsmanager get-secret-value"*)
	printf '%s\n' '{"AWS_ACCESS_KEY_ID":"AKIATESTONLY","AWS_SECRET_ACCESS_KEY":"test/secret+only","AWS_REGION":"us-east-1","AWS_DEFAULT_REGION":"us-east-1"}'
	;;
*)
	printf 'unexpected fake aws call: %s\n' "$*" >&2
	exit 64
	;;
esac
FAKE_AWS
chmod +x "$test_root/bin/aws"

cat >"$test_root/bin/sudo" <<'FAKE_SUDO'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$SUDO_TEST_LOG"
exec "$@"
FAKE_SUDO
chmod +x "$test_root/bin/sudo"

cat >"$test_root/bin/install" <<'FAKE_INSTALL'
#!/usr/bin/env bash
set -euo pipefail
filtered=()
while (($#)); do
	case "$1" in
	-o | -g)
		shift 2
		;;
	*)
		filtered+=("$1")
		shift
		;;
	esac
done
exec /usr/bin/install "${filtered[@]}"
FAKE_INSTALL
chmod +x "$test_root/bin/install"

test_path="$test_root/bin:$PATH"

acm_output=$(PATH="$test_path" "$repo_root/deploy/scripts/describe_acm_validation.sh" mediacms-dev-certificate)
test "$acm_output" = '_validation.example.com. CNAME _token.acm-validations.aws. PENDING_VALIDATION'

PATH="$test_path" "$repo_root/deploy/scripts/validate_aws_infrastructure.sh" >"$test_root/validate.out"
grep -q 'AWS infrastructure templates validated' "$test_root/validate.out"

parameter_file="$test_root/parameters.json"
printf '%s\n' '[]' >"$parameter_file"
PATH="$test_path" FAKE_STACK_EXISTS=false \
	"$repo_root/deploy/scripts/create_aws_change_set.sh" stack-create change-create "$parameter_file" \
	>"$test_root/create.out"
PATH="$test_path" FAKE_STACK_EXISTS=true \
	"$repo_root/deploy/scripts/create_aws_change_set.sh" stack-update change-update "$parameter_file" \
	>"$test_root/update.out"
grep -q -- '--change-set-type CREATE' "$aws_log"
grep -q -- '--change-set-type UPDATE' "$aws_log"
test "$(grep -c -- 'CAPABILITY_NAMED_IAM' "$aws_log")" -eq 2

describe_output=$(PATH="$test_path" "$repo_root/deploy/scripts/describe_aws_change_set.sh" stack-create change-create)
grep -q 'CREATE MediaBucket AWS::S3::Bucket Never' <<<"$describe_output"
grep -q 'CAPABILITY_NAMED_IAM' <<<"$describe_output"
if grep -Eqi '(parameter|secret|access.key)' <<<"$describe_output"; then
	printf '%s\n' 'describe output contained sensitive or parameter data' >&2
	exit 1
fi

runtime_env="$test_root/runtime.env"
extract_output=$(PATH="$test_path" "$repo_root/deploy/scripts/extract_runtime_aws_env.sh" stack-create "$runtime_env" "$(id -gn)" 2>&1)
test -f "$runtime_env"
test "$(stat -c '%a' "$runtime_env")" = 640
test "$(wc -l <"$runtime_env")" -eq 4
grep -q '^AWS_ACCESS_KEY_ID=AKIATESTONLY$' "$runtime_env"
grep -q '^AWS_SECRET_ACCESS_KEY=test/secret+only$' "$runtime_env"
grep -q '^AWS_REGION=us-east-1$' "$runtime_env"
grep -q '^AWS_DEFAULT_REGION=us-east-1$' "$runtime_env"
if grep -qE 'AKIATESTONLY|test/secret\+only' <<<"$extract_output"; then
	printf '%s\n' 'secret leaked to extraction output' >&2
	exit 1
fi
grep -q -- 'bash -c' "$sudo_log"
grep -q -- '-m 0640' "$repo_root/deploy/scripts/extract_runtime_aws_env.sh"

while IFS= read -r invocation; do
	case "$invocation" in
	*"--profile default"*"--region us-east-1"*) ;;
	*)
		printf 'missing required profile or region: %s\n' "$invocation" >&2
		exit 1
		;;
	esac
done <"$aws_log"

grep -q -- '--logical-resource-id MediaCertificate' "$aws_log"
if grep -q -- 'execute-change-set' "$aws_log"; then
	printf '%s\n' 'deployment helper executed a Change Set' >&2
	exit 1
fi

printf '%s\n' 'AWS infrastructure script tests passed'
