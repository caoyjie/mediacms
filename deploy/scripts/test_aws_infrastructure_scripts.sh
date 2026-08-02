#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

mkdir -p "$test_root/bin"
aws_log="$test_root/aws.log"
export AWS_TEST_LOG="$aws_log"

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
  *)
    printf 'unexpected fake aws call: %s\n' "$*" >&2
    exit 64
    ;;
esac
FAKE_AWS
chmod +x "$test_root/bin/aws"

output=$(PATH="$test_root/bin:$PATH" "$repo_root/deploy/scripts/describe_acm_validation.sh" mediacms-dev-certificate)

expected='_validation.example.com. CNAME _token.acm-validations.aws. PENDING_VALIDATION'
test "$output" = "$expected"
test "$(wc -l <"$aws_log")" -eq 2

while IFS= read -r invocation; do
	case "$invocation" in
	*"--profile default"*"--region us-east-1"*) ;;
	*)
		printf 'missing required profile or region: %s\n' "$invocation" >&2
		exit 1
		;;
	esac
done <"$aws_log"

grep -q -- 'cloudformation describe-stack-resource' "$aws_log"
grep -q -- '--logical-resource-id MediaCertificate' "$aws_log"
grep -q -- 'acm describe-certificate' "$aws_log"
if grep -Eq -- '(create-|update-|delete-|execute-change-set)' "$aws_log"; then
	printf '%s\n' 'validation helper attempted a mutating AWS operation' >&2
	exit 1
fi

printf '%s\n' 'AWS infrastructure script tests passed'
