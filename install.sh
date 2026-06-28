#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

say() {
  printf '\n%s\n' "$1"
}

prompt() {
  local message="$1"
  local default_value="${2:-}"
  local value
  if [[ -n "$default_value" ]]; then
    read -r -p "$message [$default_value]: " value
    printf '%s' "${value:-$default_value}"
  else
    read -r -p "$message: " value
    printf '%s' "$value"
  fi
}

prompt_secret() {
  local message="$1"
  local value
  read -r -s -p "$message (leave blank to skip): " value
  printf '\n' >&2
  printf '%s' "$value"
}

env_value() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '"%s"' "$value"
}

require_command() {
  local command_name="$1"
  local install_hint="$2"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n%s\n' "$command_name" "$install_hint" >&2
    exit 1
  fi
}

detect_lan_ip() {
  local detected=""
  if [[ -n "${WSL_DISTRO_NAME:-}" ]] && command -v powershell.exe >/dev/null 2>&1; then
    detected="$(
      powershell.exe -NoProfile -Command \
        '$route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" | Sort-Object RouteMetric | Select-Object -First 1; if ($route) { Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $route.InterfaceIndex | Where-Object { $_.IPAddress -notlike "169.254*" } | Select-Object -First 1 -ExpandProperty IPAddress }' \
        2>/dev/null | tr -d '\r' | head -n 1
    )"
  elif command -v hostname >/dev/null 2>&1 && hostname -I >/dev/null 2>&1; then
    detected="$(hostname -I 2>/dev/null | awk '{print $1}')"
  elif command -v ipconfig >/dev/null 2>&1; then
    local interface=""
    if command -v route >/dev/null 2>&1; then
      interface="$(route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}')"
    fi
    if [[ -n "$interface" ]]; then
      detected="$(ipconfig getifaddr "$interface" 2>/dev/null || true)"
    fi
    [[ -n "$detected" ]] || detected="$(ipconfig getifaddr en0 2>/dev/null || true)"
  fi
  printf '%s' "${detected:-127.0.0.1}"
}

set_env_value() {
  local file="$1"
  local key="$2"
  local value="$3"
  local temp_file
  temp_file="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { found = 0 }
    index($0, key "=") == 1 {
      if (!found) print key "=" value
      found = 1
      next
    }
    { print }
    END {
      if (!found) print key "=" value
    }
  ' "$file" >"$temp_file"
  mv "$temp_file" "$file"
}

current_public_host() {
  local env_file="$1"
  local url
  [[ -f "$env_file" ]] || return 0
  url="$(sed -n 's/^APP_PUBLIC_URL=//p' "$env_file" | tail -n 1)"
  url="${url%\"}"
  url="${url#\"}"
  url="${url#*://}"
  url="${url%%/*}"
  printf '%s' "${url%:*}"
}

current_port() {
  local config_file="$1"
  [[ -f "$config_file" ]] || return 0
  sed -n 's/^PORT=//p' "$config_file" | tail -n 1
}

say "VocaFSRS interactive setup"
printf '%s\n' "This installer builds the app, creates the database, and can import one TXT/CSV dataset."

require_command uv "Install uv from https://docs.astral.sh/uv/getting-started/installation/ and run this installer again."
require_command node "Install Node.js 20.19+ or 22.12+ from https://nodejs.org/ and run this installer again."
require_command npm "npm is normally included with Node.js."

node_version="$(node -p 'process.versions.node')"
IFS=. read -r node_major node_minor _ <<<"$node_version"
if ! ((node_major == 20 && node_minor >= 19 || node_major >= 22 && (node_major > 22 || node_minor >= 12))); then
  echo "Node.js 20.19+ or 22.12+ is required. Current version: $(node --version)" >&2
  exit 1
fi

env_file="$ROOT_DIR/backend/.env"
runtime_config="$ROOT_DIR/.vocafsrs.conf"
default_ip="$(current_public_host "$env_file")"
default_port="$(current_port "$runtime_config")"
lan_ip="$(prompt "IP or hostname used by another device" "${default_ip:-$(detect_lan_ip)}")"
port="$(prompt "Web port" "${default_port:-8080}")"
if [[ ! "$port" =~ ^[0-9]+$ ]] || ((port < 1 || port > 65535)); then
  echo "Port must be a number from 1 to 65535." >&2
  exit 1
fi

replace_env="yes"
if [[ -f "$env_file" ]]; then
  overwrite="$(prompt "backend/.env already exists. Replace it? (y/N)" "N")"
  if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
    replace_env="no"
    echo "Keeping the existing backend configuration."
  fi
fi

public_url="http://${lan_ip}:${port}"
if [[ "$replace_env" == "yes" ]]; then
  timezone="$(prompt "Report timezone" "Asia/Taipei")"
  openrouter_key="$(prompt_secret "OpenRouter API key")"
  google_key="$(prompt_secret "Gemini API key (fallback)")"
  if [[ -z "$openrouter_key" && -z "$google_key" ]]; then
    printf '%s\n' "Warning: formal review answers cannot be graded until an LLM key is added to backend/.env."
  fi
  discord_webhook="$(prompt_secret "Discord webhook URL for due-review reminders")"

  env_contents="$(cat <<EOF
VOCAB_ENV=production
DATABASE_URL=
DATABASE_PATH=data/vocab.db
ALLOWED_ORIGINS=$(env_value "$public_url")
OPENROUTER_API_KEY=$(env_value "$openrouter_key")
OPENROUTER_MODEL=openrouter/owl-alpha
OPENROUTER_SITE_URL=$(env_value "$public_url")
OPENROUTER_APP_NAME=VocaFSRS
GOOGLE_API_KEY=$(env_value "$google_key")
LLM_MODEL=gemini-2.5-flash
LLM_TIMEOUT_SECONDS=45
REPORT_TIMEZONE=$(env_value "$timezone")
DISCORD_WEBHOOK_URL=$(env_value "$discord_webhook")
APP_PUBLIC_URL=$(env_value "$public_url")
NOTIFICATION_POLL_SECONDS=60
EOF
)"
fi

say "Installing backend dependencies"
(cd "$ROOT_DIR/backend" && uv sync --no-dev)

say "Installing and building the frontend"
(cd "$ROOT_DIR/frontend" && npm ci && npm run build)

backup_dir="$(mktemp -d)"
env_existed="no"
config_existed="no"
if [[ -f "$env_file" ]]; then
  cp "$env_file" "$backup_dir/backend.env"
  env_existed="yes"
fi
if [[ -f "$runtime_config" ]]; then
  cp "$runtime_config" "$backup_dir/runtime.conf"
  config_existed="yes"
fi

restore_config() {
  if [[ "$env_existed" == "yes" ]]; then
    cp "$backup_dir/backend.env" "$env_file"
  else
    rm -f "$env_file"
  fi
  if [[ "$config_existed" == "yes" ]]; then
    cp "$backup_dir/runtime.conf" "$runtime_config"
  else
    rm -f "$runtime_config"
  fi
  rm -rf "$backup_dir"
  printf '%s\n' "Installation failed; previous configuration was restored." >&2
}
trap restore_config ERR

if [[ "$replace_env" == "yes" ]]; then
  printf '%s\n' "$env_contents" >"$env_file"
else
  set_env_value "$env_file" "ALLOWED_ORIGINS" "$(env_value "$public_url")"
  set_env_value "$env_file" "OPENROUTER_SITE_URL" "$(env_value "$public_url")"
  set_env_value "$env_file" "APP_PUBLIC_URL" "$(env_value "$public_url")"
fi
chmod 600 "$env_file"

cat >"$runtime_config" <<EOF
HOST=0.0.0.0
PORT=$port
EOF

say "Updating the database"
(cd "$ROOT_DIR/backend" && uv run alembic upgrade head)

trap - ERR
rm -rf "$backup_dir"

while true; do
  dataset_path="$(prompt "Dataset path to import now (.txt/.csv, blank to import later in the app)" "")"
  [[ -n "$dataset_path" ]] || break
  dataset_path="${dataset_path/#\~/$HOME}"
  say "Importing vocabulary"
  if (cd "$ROOT_DIR/backend" && PYTHONPATH=. uv run python scripts/import_vocabulary.py "$dataset_path"); then
    break
  fi
  retry_import="$(prompt "Import failed. Try another file? (Y/n)" "Y")"
  [[ ! "$retry_import" =~ ^[Nn]$ ]] || break
done

say "Installation complete"
printf 'Open: %s\n' "$public_url"
printf '%s\n' "Start later: ./start.sh"
printf '%s\n' "Stop: press Ctrl+C in the server terminal"
printf '%s\n' "Dataset files may stay anywhere; imported vocabulary is stored in backend/data/vocab.db."

start_now="$(prompt "Start VocaFSRS now? (Y/n)" "Y")"
if [[ ! "$start_now" =~ ^[Nn]$ ]]; then
  exec bash "$ROOT_DIR/start.sh"
fi
