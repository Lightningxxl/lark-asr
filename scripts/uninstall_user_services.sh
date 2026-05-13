#!/usr/bin/env bash
set -euo pipefail

SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if [[ -z "${XDG_RUNTIME_DIR:-}" && -d "/run/user/$(id -u)" ]]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi

systemctl --user disable --now lark-asr-hook.service lark-asr-worker.service 2>/dev/null || true
rm -f "$SYSTEMD_USER_DIR/lark-asr-hook.service" "$SYSTEMD_USER_DIR/lark-asr-worker.service"
systemctl --user daemon-reload
echo "removed lark-asr user services"
