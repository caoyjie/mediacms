#!/usr/bin/env bash

set -u

MEDIACMS_RUN_NETWORK_TEST=0

usage() {
    cat <<'EOF'
Usage: probe_ubuntu_production_environment.sh [--network] [--help]

Collects a read-only Ubuntu production-environment report from the current
machine and, when run inside MediaCMS, a safe project summary.

Options:
  --network  Download 10 MB from Cloudflare to measure network throughput.
  --help     Show this help message.

The script does not read .env, cookies, AWS credentials, or tunnel tokens.
EOF
}

while (($#)); do
    case "$1" in
        --network) MEDIACMS_RUN_NETWORK_TEST=1 ;;
        --help|-h) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

section() {
    printf '\n=== %s ===\n' "$1"
}

has_command() {
    command -v "$1" >/dev/null 2>&1
}

section 'Ubuntu production environment'
printf 'Collected at: '
date --iso-8601=seconds 2>/dev/null || date

section 'OS'
uname -a
sed -n '1,20p' /etc/os-release 2>/dev/null || true
printf 'Architecture: %s\n' "$(uname -m)"
printf 'Timezone: '
timedatectl show --property=Timezone --value 2>/dev/null || printf 'unavailable\n'

section 'CPU'
lscpu 2>/dev/null | grep -E '^(Architecture|CPU\(s\)|On-line CPU|Model name|Thread|Core|Socket|CPU max MHz|CPU min MHz|Virtualization):' || true
printf 'Available processors: '
nproc 2>/dev/null || printf 'unavailable\n'

section 'Memory and swap'
free -h 2>/dev/null || true
swapon --show 2>/dev/null || true

section 'Storage'
lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS 2>/dev/null || true
df -hT . 2>/dev/null || true
df -ih . 2>/dev/null || true

section 'Resource limits'
printf 'Open files: %s\n' "$(ulimit -n)"
printf 'Processes: %s\n' "$(ulimit -u)"
sysctl vm.overcommit_memory 2>/dev/null || true
sysctl fs.inotify.max_user_watches 2>/dev/null || true
sysctl fs.inotify.max_user_instances 2>/dev/null || true

section 'Network devices'
ip -brief link 2>/dev/null || true
printf 'Addresses and public IPs are intentionally omitted.\n'

section 'Runtime versions'
git --version 2>/dev/null || true
python3 --version 2>/dev/null || true
node --version 2>/dev/null || true
npm --version 2>/dev/null || true
docker --version 2>/dev/null || true
docker compose version 2>/dev/null || true
aws --version 2>&1 || true
cloudflared --version 2>/dev/null || true
ffmpeg -version 2>/dev/null | sed -n '1,3p' || true
yt-dlp --version 2>/dev/null || true

section 'Ubuntu packages'
if has_command dpkg-query; then
    dpkg-query -W -f='${Package} ${Version}\n' \
        docker-ce docker.io docker-compose-plugin postgresql redis-server \
        python3 nodejs ffmpeg awscli cloudflared 2>&1 || true
else
    printf 'dpkg-query is unavailable.\n'
fi

section 'Container capacity'
docker info --format 'CPUs={{.NCPU}} Memory={{.MemTotal}} StorageDriver={{.Driver}} CgroupDriver={{.CgroupDriver}} CgroupVersion={{.CgroupVersion}}' 2>&1 || true
docker system df 2>&1 || true

section 'Relevant services'
if has_command systemctl; then
    systemctl --no-pager --type=service --state=running 2>/dev/null | grep -E 'docker|containerd|postgres|redis|celery|gunicorn|nginx|cloudflared' || true
    printf 'System state: '
    systemctl is-system-running 2>/dev/null || true
else
    printf 'systemctl is unavailable.\n'
fi

section 'Listening service ports'
ss -lntup 2>/dev/null | grep -E ':(80|443|5432|6379|8000|8080|8088)\b' || true

section 'Logs and OOM history'
journalctl --disk-usage 2>/dev/null || true
journalctl -k --no-pager -g 'Out of memory|Killed process|oom-kill' -n 30 2>/dev/null || true

section 'Display environment'
printf 'DISPLAY=%s\n' "${DISPLAY:-not-set}"
printf 'WAYLAND_DISPLAY=%s\n' "${WAYLAND_DISPLAY:-not-set}"
printf 'XDG_SESSION_TYPE=%s\n' "${XDG_SESSION_TYPE:-not-set}"

section 'MediaCMS repository'
printf 'Working directory: %s\n' "$PWD"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git status --short --branch
    git log -1 --oneline
    du -sh . 2>/dev/null || true
    du -sh frontend frontend-tools static media 2>/dev/null || true
else
    printf 'The current directory is not a Git worktree.\n'
fi

section 'Compose services'
if [[ -f compose.yml || -f compose.yaml || -f docker-compose.yml || -f docker-compose.yaml ]]; then
    docker compose config --services 2>&1 || true
    docker compose images 2>&1 || true
else
    printf 'No Compose file exists in the current directory.\n'
fi

section 'Optional network test'
if ((MEDIACMS_RUN_NETWORK_TEST)); then
    curl --fail --location --output /dev/null \
        --write-out 'HTTP=%{http_code} DNS=%{time_namelookup}s Connect=%{time_connect}s Start=%{time_starttransfer}s Total=%{time_total}s Speed=%{speed_download}B/s\n' \
        'https://speed.cloudflare.com/__down?bytes=10000000' || true
else
    printf 'Skipped. Run again with --network to download a 10 MB test file.\n'
fi

section 'Privacy reminder'
printf 'Review output before sharing. No secrets were intentionally collected.\n'
