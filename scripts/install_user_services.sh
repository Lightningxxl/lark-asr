#!/usr/bin/env bash
set -euo pipefail

ROOT="${LARK_ASR_HOME:-$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
CONFIG="${LARK_ASR_CONFIG:-$ROOT/config.toml}"
INTERVAL="${LARK_ASR_WORKER_INTERVAL:-20}"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if [[ -z "${XDG_RUNTIME_DIR:-}" && -d "/run/user/$(id -u)" ]]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl is required for user services" >&2
  exit 1
fi

if [[ ! -f "$CONFIG" ]]; then
  echo "missing config: $CONFIG" >&2
  echo "copy config.example.toml to config.toml and edit it first" >&2
  exit 1
fi

"$ROOT/bin/lark-asr" init --config "$CONFIG"
mkdir -p "$SYSTEMD_USER_DIR"

cat > "$SYSTEMD_USER_DIR/lark-asr-hook.service" <<SERVICE
[Unit]
Description=lark-asr Feishu event hook
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
Environment=PYTHONUNBUFFERED=1
ExecStart=$ROOT/bin/lark-asr hook --config $CONFIG
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
SERVICE

cat > "$SYSTEMD_USER_DIR/lark-asr-worker.service" <<SERVICE
[Unit]
Description=lark-asr meeting transcript worker
After=network-online.target lark-asr-hook.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
Environment=PYTHONUNBUFFERED=1
ExecStart=$ROOT/bin/lark-asr worker --config $CONFIG --interval $INTERVAL
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
SERVICE

systemctl --user daemon-reload
systemctl --user enable --now lark-asr-hook.service lark-asr-worker.service

echo "installed and started:"
systemctl --user --no-pager --full status lark-asr-hook.service lark-asr-worker.service || true
