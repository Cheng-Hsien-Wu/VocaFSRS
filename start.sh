#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$ROOT_DIR/.vocafsrs.conf"
ENV_FILE="$ROOT_DIR/backend/.env"

host="0.0.0.0"
port="8080"
if [[ -f "$CONFIG_FILE" ]]; then
  configured_host="$(sed -n 's/^HOST=//p' "$CONFIG_FILE" | tail -n 1)"
  configured_port="$(sed -n 's/^PORT=//p' "$CONFIG_FILE" | tail -n 1)"
  [[ -n "$configured_host" ]] && host="$configured_host"
  [[ -n "$configured_port" ]] && port="$configured_port"
fi

if [[ ! "$port" =~ ^[0-9]+$ ]] || ((port < 1 || port > 65535)); then
  echo "Invalid PORT in .vocafsrs.conf: $port" >&2
  exit 1
fi

public_url=""
if [[ -f "$ENV_FILE" ]]; then
  public_url="$(sed -n 's/^APP_PUBLIC_URL=//p' "$ENV_FILE" | tail -n 1)"
  public_url="${public_url%\"}"
  public_url="${public_url#\"}"
fi
printf 'Open: %s\n' "${public_url:-http://localhost:$port}"

cd "$ROOT_DIR/backend"
exec uv run uvicorn main:app --host "$host" --port "$port"
