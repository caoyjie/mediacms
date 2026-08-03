#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$script_dir/../.." && pwd)"

postgres_container="${POSTGRES_CONTAINER:-mediacms-aws-test-postgres}"
redis_container="${REDIS_CONTAINER:-mediacms-redis-1}"
postgres_host="${POSTGRES_HOST:-127.0.0.1}"
postgres_port="${POSTGRES_PORT:-55432}"
postgres_name="${POSTGRES_NAME:-mediacms_test}"
postgres_user="${POSTGRES_USER:-mediacms_test}"
postgres_password="${POSTGRES_PASSWORD:-mediacms_test_local_only}"

fail() {
	printf 'Error: %s\n' "$1" >&2
	exit 1
}

command -v docker >/dev/null 2>&1 || fail 'docker is required'
test -x "$repo_dir/.venv/bin/pytest" || fail "missing pytest at $repo_dir/.venv/bin/pytest"

docker inspect "$postgres_container" >/dev/null 2>&1 ||
	fail "PostgreSQL container '$postgres_container' is not present"
postgres_health="$(docker inspect --format '{{.State.Health.Status}}' "$postgres_container" 2>/dev/null || true)"
test "$postgres_health" = healthy ||
	fail "PostgreSQL container '$postgres_container' is not healthy (status: ${postgres_health:-unknown})"

docker inspect "$redis_container" >/dev/null 2>&1 ||
	fail "Redis container '$redis_container' is not present"
redis_ip="$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$redis_container")"
test -n "$redis_ip" || fail "could not determine an IP address for '$redis_container'"

cd "$repo_dir"
export POSTGRES_HOST="$postgres_host"
export POSTGRES_PORT="$postgres_port"
export POSTGRES_NAME="$postgres_name"
export POSTGRES_USER="$postgres_user"
export POSTGRES_PASSWORD="$postgres_password"
export REDIS_LOCATION="${REDIS_LOCATION:-redis://${redis_ip}:6379/1}"

printf 'Running backend tests with PostgreSQL %s:%s and Redis %s...\n' \
	"$POSTGRES_HOST" "$POSTGRES_PORT" "$redis_ip"
exec "$repo_dir/.venv/bin/pytest" -q "$@"
