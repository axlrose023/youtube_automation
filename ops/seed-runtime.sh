#!/usr/bin/env bash

set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

require_file "$BACKEND_ENV_FILE"
load_env_file "$BACKEND_ENV_FILE"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is missing. Run ./ops/bootstrap-ubuntu.sh first." >&2
  exit 1
fi

if [[ -z "${YTA_ADMIN_PASSWORD:-}" ]]; then
  echo "YTA_ADMIN_PASSWORD is required." >&2
  exit 1
fi

YTA_ADMIN_USERNAME="${YTA_ADMIN_USERNAME:-admin}"
YTA_ADMIN_ACTIVE="${YTA_ADMIN_ACTIVE:-true}"
YTA_PROXY_SCHEME="${YTA_PROXY_SCHEME:-socks5}"
YTA_PROXY_ACTIVE="${YTA_PROXY_ACTIVE:-true}"

sql_quote() {
  local value="${1//\'/\'\'}"
  printf "'%s'" "$value"
}

sql_bool() {
  case "${1,,}" in
    1|true|yes|on) printf 'true' ;;
    0|false|no|off) printf 'false' ;;
    *)
      echo "Invalid boolean value: $1" >&2
      exit 1
      ;;
  esac
}

sql_nullable() {
  local value="${1:-}"
  if [[ -z "$value" ]]; then
    printf 'NULL'
    return
  fi
  sql_quote "$value"
}

echo "Ensuring admin user '$YTA_ADMIN_USERNAME'"
compose exec -T api \
  uv run cli ensure_user \
  --username "$YTA_ADMIN_USERNAME" \
  --password "$YTA_ADMIN_PASSWORD" \
  --admin \
  --active

cat > "$ROOT_DIR/deploy/admin.credentials" <<EOF
username=$YTA_ADMIN_USERNAME
password=$YTA_ADMIN_PASSWORD
EOF
chmod 600 "$ROOT_DIR/deploy/admin.credentials"
echo "Wrote deploy/admin.credentials"

proxy_seed_requested=0
for var_name in YTA_PROXY_ID YTA_PROXY_LABEL YTA_PROXY_HOST YTA_PROXY_PORT; do
  if [[ -n "${!var_name:-}" ]]; then
    proxy_seed_requested=1
    break
  fi
done

if [[ "$proxy_seed_requested" -eq 1 ]]; then
  for var_name in YTA_PROXY_ID YTA_PROXY_LABEL YTA_PROXY_HOST YTA_PROXY_PORT; do
    if [[ -z "${!var_name:-}" ]]; then
      echo "$var_name is required when proxy seeding is enabled." >&2
      exit 1
    fi
  done

  if [[ ! "${YTA_PROXY_PORT}" =~ ^[0-9]+$ ]]; then
    echo "YTA_PROXY_PORT must be an integer." >&2
    exit 1
  fi

  echo "Upserting proxy '$YTA_PROXY_LABEL'"

  proxy_id_sql="$(sql_quote "$YTA_PROXY_ID")"
  proxy_label_sql="$(sql_quote "$YTA_PROXY_LABEL")"
  proxy_scheme_sql="$(sql_quote "$YTA_PROXY_SCHEME")"
  proxy_host_sql="$(sql_quote "$YTA_PROXY_HOST")"
  proxy_port_sql="$YTA_PROXY_PORT"
  proxy_username_sql="$(sql_nullable "${YTA_PROXY_USERNAME:-}")"
  proxy_password_sql="$(sql_nullable "${YTA_PROXY_PASSWORD:-}")"
  proxy_country_sql="$(sql_nullable "${YTA_PROXY_COUNTRY_CODE:-}")"
  proxy_notes_sql="$(sql_nullable "${YTA_PROXY_NOTES:-}")"
  proxy_active_sql="$(sql_bool "$YTA_PROXY_ACTIVE")"

  compose exec -T postgres psql \
    -U "${APP__POSTGRES__USER}" \
    -d "${APP__POSTGRES__DB}" \
    -v ON_ERROR_STOP=1 \
    -c "
      insert into proxies (
        id,
        label,
        scheme,
        host,
        port,
        username,
        password,
        country_code,
        notes,
        is_active
      )
      values (
        ${proxy_id_sql},
        ${proxy_label_sql},
        ${proxy_scheme_sql},
        ${proxy_host_sql},
        ${proxy_port_sql},
        ${proxy_username_sql},
        ${proxy_password_sql},
        ${proxy_country_sql},
        ${proxy_notes_sql},
        ${proxy_active_sql}
      )
      on conflict (id) do update set
        label = excluded.label,
        scheme = excluded.scheme,
        host = excluded.host,
        port = excluded.port,
        username = excluded.username,
        password = excluded.password,
        country_code = excluded.country_code,
        notes = excluded.notes,
        is_active = excluded.is_active,
        updated_at = current_timestamp;
    "
fi

echo "Runtime seed completed."
