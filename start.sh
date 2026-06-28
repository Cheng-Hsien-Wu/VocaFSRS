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
  if [[ -n "$configured_host" ]]; then
    host="$configured_host"
  fi
  if [[ -n "$configured_port" ]]; then
    port="$configured_port"
  fi
fi

if [[ ! "$port" =~ ^[0-9]+$ ]] || ((port < 1 || port > 65535)); then
  echo "Invalid PORT in .vocafsrs.conf: $port" >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed. Run the installer first." >&2
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "backend/.env is missing. Run the installer first." >&2
  exit 1
fi
if [[ ! -f "$ROOT_DIR/frontend/dist/index.html" ]]; then
  echo "The frontend build is missing. Run the installer first." >&2
  exit 1
fi

if ! uv run python - "$host" "$port" <<'PY'
import socket
import sys

host, port = sys.argv[1], int(sys.argv[2])
with socket.socket() as sock:
    sock.bind((host, port))
PY
then
  echo "Port $port is already in use. Stop the other service or rerun the installer with another port." >&2
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
