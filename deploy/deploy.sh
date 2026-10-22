#!/usr/bin/env bash
# Run on EC2: sudo bash /opt/ai4sbench/deploy/deploy.sh [main-commit-sha]
set -Eeuo pipefail
umask 077

repo=/opt/ai4sbench
service_user=ai4sbench
env_file=/etc/ai4sbench/control-panel.env
python="$repo/backend/.venv/bin/python"
public_url=https://dashboard.ai4sbench.org
step=preflight
snapshot='not created'

git_service() { sudo -n -u "$service_user" git -C "$repo" "$@"; }
as_service() { sudo -n -u "$service_user" "$@"; }
with_environment() {
    # Source secrets only in this subprocess; do not enable shell tracing.
    bash -c 'set -e; set -a; . "$1"; set +a; shift; exec runuser -u "$1" -- "${@:2}"' \
        deploy "$env_file" "$service_user" "$@"
}
stage() { step=$1; printf '\n[%s] %s\n' "$(date -u +%FT%TZ)" "$step"; }
fail() { printf 'Deployment stopped: %s\n' "$*" >&2; return 1; }
check_checkout() {
    git_service diff --quiet --ignore-submodules=none || fail 'tracked files or submodules changed'
    git_service diff --cached --quiet || fail 'staged changes exist'
    local unexpected
    unexpected=$(git_service ls-files --others --exclude-standard | sed '\#^repository/#d')
    [[ -z "$unexpected" ]] || fail "unexpected untracked files: $unexpected"
    git_service submodule foreach --quiet --recursive 'test -z "$(git status --porcelain --untracked-files=all)"'
}

main() {
    if [[ ${1:-} == --help ]]; then
        printf 'Run on EC2: sudo bash %s/deploy/deploy.sh [main-commit-sha]\n' "$repo"
        return
    fi
    [[ $# -le 1 ]] || fail 'expected at most one main commit SHA'
    [[ $# -eq 0 || $1 =~ ^[0-9a-f]{7,40}$ ]] || fail 'expected a main commit SHA'
    [[ $EUID -eq 0 ]] || fail 'run with sudo on EC2'
    exec 9>/var/lock/ai4sbench-deploy.lock
    flock -n 9 || fail 'another deployment is running'
    mkdir -p /var/lib/ai4sbench/deployments
    local log started old target uv
    started=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
    log="/var/lib/ai4sbench/deployments/$(date -u +%Y%m%dT%H%M%S)-$$.log"
    exec > >(tee -a "$log") 2>&1
    trap 'printf "FAILED at %s; snapshot: %s; log: %s\n" "$step" "$snapshot" "$log" >&2' ERR
    printf 'Deployment log: %s\n' "$log"
    cd "$repo/backend"

    stage preflight
    check_checkout
    [[ $(git_service branch --show-current) == main ]] || fail 'EC2 checkout must be on main'
    old=$(git_service rev-parse HEAD)
    git_service fetch origin main
    target=$(git_service rev-parse --verify "${1:-origin/main}^{commit}")
    git_service merge-base --is-ancestor "$target" origin/main
    git_service merge-base --is-ancestor "$old" "$target"
    [[ -z $(git_service ls-tree -r --name-only "$target" -- repository) ]] \
        || fail 'target collides with runtime repository/ directory'
    printf 'Parent: %s -> %s\n' "$old" "$target"
    local units=(ai4sbench-api.service ai4sbench-jobs.service)
    if systemctl is-enabled --quiet ai4sbench-harbor-v2-jobs.service \
        || systemctl is-active --quiet ai4sbench-harbor-v2-jobs.service; then
        units+=(ai4sbench-harbor-v2-jobs.service)
    fi
    local dependencies_changed=false
    if ! git_service diff --quiet "$old" "$target" -- backend/uv.lock backend/pyproject.toml; then
        dependencies_changed=true
        uv=$(command -v uv) || fail 'uv is required when dependencies change'
        as_service "$uv" --version
    fi

    stage database_snapshot
    snapshot=$(with_environment "$python" - <<'PY'
import sqlite3
from pathlib import Path
from sqlalchemy.engine import make_url
from src.core.config import Settings
from src.control_panel.api.v1.routes import _create_database_snapshot

settings = Settings(_env_file=None)
url = make_url(settings.database_url)
assert settings.environment == "production", "Expected production settings"
assert url.drivername == "sqlite", "Expected SQLite"
assert url.database == "/var/lib/ai4sbench/control-panel.sqlite3", "Unexpected database path"
item = _create_database_snapshot(settings)
path = Path(url.database).parent / "cache/sqlite-snapshots" / item["name"]
with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
    assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)], "Invalid snapshot"
print(path)
PY
    )
    printf 'SQLite snapshot: %s\n' "$snapshot"
    with_environment "$python" -m alembic current

    stage update_checkout
    git_service merge --ff-only "$target"
    git_service submodule sync --recursive
    git_service submodule update --init --recursive
    check_checkout
    [[ $(git_service rev-parse HEAD) == "$target" ]] || fail 'HEAD differs from target'
    printf 'Website gitlink: %s\n' "$(git_service rev-parse HEAD:ai4s-bench-website)"

    stage dependencies
    if "$dependencies_changed"; then
        local extras=(--extra dev --extra aws)
        if [[ ${#units[@]} -eq 3 ]]; then extras+=(--extra harbor-ec2); fi
        as_service "$uv" sync --frozen "${extras[@]}"
    fi

    stage migrations
    with_environment "$python" -m alembic upgrade head
    with_environment "$python" -m alembic current

    stage restart_services
    systemctl restart "${units[@]}"
    systemctl is-active "${units[@]}"

    stage verify
    curl -fsS --retry 15 --retry-connrefused --retry-delay 1 --max-time 5 \
        http://127.0.0.1:8080/health/ready
    /usr/bin/python3 "$repo/deploy/verify.py" "$repo" "$public_url"
    local journal_args=() unit
    for unit in "${units[@]}"; do journal_args+=(-u "$unit"); done
    journalctl "${journal_args[@]}" --since "$started" --no-pager -p warning
    printf '\nSUCCESS parent=%s website=%s\nSnapshot: %s\nLog: %s\n' \
        "$target" "$(git_service rev-parse HEAD:ai4s-bench-website)" "$snapshot" "$log"
}

# Parse the full function before Git updates this script's checkout.
main "$@"
