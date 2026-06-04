#!/usr/bin/env bash
# Push the current branch and run the production deploy script on the server.
#
# Required for password auth:
#   export YTA_SERVER_PASSWORD='...'
# or:
#   export SSHPASS='...'

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SERVER_HOST="${YTA_SERVER_HOST:-195.123.219.89}"
SERVER_PORT="${YTA_SERVER_PORT:-3333}"
SERVER_USER="${YTA_SERVER_USER:-root}"
SERVER_DIR="${YTA_SERVER_DIR:-/opt/youtube_automation}"
PUBLIC_URL="${YTA_PUBLIC_URL:-https://and-remu.ourdocumwiki.live}"
STRICT_HOST_KEY_CHECKING="${YTA_SSH_STRICT_HOST_KEY_CHECKING:-no}"

BRANCH=""
SKIP_PUSH=0
DRY_RUN=0
CHECK_ONLY=0

usage() {
  cat <<'EOF'
Usage: ops/deploy-remote.sh [options]

Options:
  --branch <name>       Deploy this branch instead of the current branch.
  --skip-push           Do not run git push before remote deploy.
  --check               Check server access, commit, API, and services without deploy.
  --dry-run             Print the resolved deploy plan without changing anything.
  -h, --help            Show this help.

Environment:
  YTA_SERVER_HOST       Default: 195.123.219.89
  YTA_SERVER_PORT       Default: 3333
  YTA_SERVER_USER       Default: root
  YTA_SERVER_DIR        Default: /opt/youtube_automation
  YTA_PUBLIC_URL        Default: https://and-remu.ourdocumwiki.live
  YTA_SERVER_PASSWORD   Optional SSH password. SSHPASS is also supported.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch)
      BRANCH="${2:-}"
      if [[ -z "$BRANCH" ]]; then
        echo "Missing value for --branch" >&2
        exit 2
      fi
      shift 2
      ;;
    --skip-push)
      SKIP_PUSH=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --check)
      CHECK_ONLY=1
      SKIP_PUSH=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

cd "$ROOT_DIR"

if [[ -z "$BRANCH" ]]; then
  BRANCH="$(git branch --show-current)"
fi

if [[ -z "$BRANCH" ]]; then
  echo "Cannot determine current git branch. Pass --branch <name>." >&2
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Tracked changes are not committed. Commit or stash them before deploy." >&2
  git status --short
  exit 1
fi

LOCAL_COMMIT="$(git rev-parse HEAD)"
LOCAL_COMMIT_SHORT="$(git rev-parse --short HEAD)"
REMOTE="${SERVER_USER}@${SERVER_HOST}"

SSH_CMD=(
  ssh
  -p "$SERVER_PORT"
  -o "StrictHostKeyChecking=${STRICT_HOST_KEY_CHECKING}"
  -o "UserKnownHostsFile=${YTA_SSH_USER_KNOWN_HOSTS_FILE:-/dev/null}"
)

if [[ -n "${YTA_SERVER_PASSWORD:-}" && -z "${SSHPASS:-}" ]]; then
  export SSHPASS="$YTA_SERVER_PASSWORD"
fi

if [[ -n "${SSHPASS:-}" ]]; then
  if ! command -v sshpass >/dev/null 2>&1; then
    echo "SSHPASS/YTA_SERVER_PASSWORD is set, but sshpass is not installed." >&2
    exit 1
  fi
  SSH_CMD=(sshpass -e "${SSH_CMD[@]}")
fi

echo "==> deploy plan"
echo "    branch:       $BRANCH"
echo "    commit:       $LOCAL_COMMIT_SHORT"
echo "    server:       $REMOTE:$SERVER_PORT"
echo "    server dir:   $SERVER_DIR"
echo "    public url:   $PUBLIC_URL"
echo "    mode:         $([[ "$CHECK_ONLY" -eq 1 ]] && echo check || echo deploy)"
echo "    push:         $([[ "$SKIP_PUSH" -eq 1 ]] && echo no || echo yes)"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "==> dry run only"
  exit 0
fi

if [[ "$SKIP_PUSH" -ne 1 ]]; then
  echo "==> git push origin $BRANCH"
  git push origin "$BRANCH"
fi

echo "==> remote deploy"
"${SSH_CMD[@]}" "$REMOTE" "bash -s" -- "$BRANCH" "$SERVER_DIR" "$LOCAL_COMMIT" "$PUBLIC_URL" "$CHECK_ONLY" <<'REMOTE_SCRIPT'
set -euo pipefail

branch="$1"
server_dir="$2"
expected_commit="$3"
public_url="${4%/}"
check_only="$5"

cd "$server_dir"

if [[ "$check_only" == "1" ]]; then
  echo "==> server check"
  current_branch="$(git branch --show-current)"
  actual_commit="$(git rev-parse HEAD)"
  if [[ "$current_branch" != "$branch" ]]; then
    echo "Server branch mismatch." >&2
    echo "  expected: $branch" >&2
    echo "  actual:   $current_branch" >&2
    exit 1
  fi
  if [[ "$actual_commit" != "$expected_commit" ]]; then
    echo "Server commit mismatch." >&2
    echo "  expected: $expected_commit" >&2
    echo "  actual:   $actual_commit" >&2
    exit 1
  fi
  curl -fsS "$public_url/api/ping" >/dev/null
  systemctl is-active --quiet yta-android-worker.service
  systemctl is-active --quiet yta-appium.service
  systemctl is-active --quiet yta-android-display.service
  if [[ -d /opt/yta_sync_repo ]]; then
    echo "Unexpected active duplicate checkout: /opt/yta_sync_repo" >&2
    exit 1
  fi
  echo "==> remote check ok [$branch $(git rev-parse --short HEAD)]"
  exit 0
fi

echo "==> server git sync"
git fetch origin "$branch"
git checkout "$branch"
git pull --ff-only origin "$branch"

echo "==> server deploy"
./ops/deploy.sh "$branch"

actual_commit="$(git rev-parse HEAD)"
if [[ "$actual_commit" != "$expected_commit" ]]; then
  echo "Server commit mismatch." >&2
  echo "  expected: $expected_commit" >&2
  echo "  actual:   $actual_commit" >&2
  exit 1
fi

echo "==> server health checks"
curl -fsS "$public_url/api/ping" >/dev/null
systemctl is-active --quiet yta-android-worker.service
systemctl is-active --quiet yta-appium.service
systemctl is-active --quiet yta-android-display.service

if [[ -d /opt/yta_sync_repo ]]; then
  echo "Unexpected active duplicate checkout: /opt/yta_sync_repo" >&2
  exit 1
fi

echo "==> remote deploy ok [$branch $(git rev-parse --short HEAD)]"
REMOTE_SCRIPT
