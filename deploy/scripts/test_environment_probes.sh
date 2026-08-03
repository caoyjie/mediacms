#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
scripts=(
    "$script_dir/probe_arch_test_environment.sh"
    "$script_dir/probe_ubuntu_production_environment.sh"
)

test_script="$script_dir/run_backend_tests.sh"
test -f "$test_script"
test -x "$test_script"
bash -n "$test_script"
grep -Fq 'REDIS_LOCATION' "$test_script"
grep -Fq 'docker inspect' "$test_script"
grep -Fq 'POSTGRES_PORT' "$test_script"

for script in "${scripts[@]}"; do
    test -f "$script"
    test -x "$script"
    bash -n "$script"

    help_output="$(bash "$script" --help)"
    grep -Fq -- '--network' <<<"$help_output"
    grep -Fq -- 'read-only' <<<"$help_output"

    grep -Fq 'MEDIACMS_RUN_NETWORK_TEST' "$script"
    grep -Fq 'https://speed.cloudflare.com/__down?bytes=10000000' "$script"
    grep -Fq 'git status --short --branch' "$script"
    grep -Fq 'docker compose config --services' "$script"

    if grep -Eq '(cat|source|grep|sed).*(\.env|cookies\.txt|credentials|config\.json)' "$script"; then
        printf 'Sensitive configuration access found in %s\n' "$script" >&2
        exit 1
    fi
done

grep -Fq 'pacman -Q' "${scripts[0]}"
grep -Fq 'lspci' "${scripts[0]}"
grep -Fq 'ffmpeg -hide_banner -hwaccels' "${scripts[0]}"

grep -Fq 'dpkg-query' "${scripts[1]}"
grep -Fq 'systemctl is-system-running' "${scripts[1]}"
grep -Fq 'journalctl -k' "${scripts[1]}"

printf 'Environment probe script contract checks passed.\n'
