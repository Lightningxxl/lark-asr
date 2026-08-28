#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_FILE="$SYSTEMD_USER_DIR/lark-asr-gpu-watchdog.service"
TIMER_FILE="$SYSTEMD_USER_DIR/lark-asr-gpu-watchdog.timer"

mkdir -p "$SYSTEMD_USER_DIR"

printf '%s\n' \
  '[Unit]' \
  'Description=Recover the Lark ASR worker GPU runtime' \
  'After=docker.service' \
  '' \
  '[Service]' \
  'Type=oneshot' \
  "Environment=LARK_ASR_PROJECT_DIR=$PROJECT_DIR" \
  "ExecStart=$PROJECT_DIR/scripts/gpu_watchdog.sh" \
  > "$SERVICE_FILE"

printf '%s\n' \
  '[Unit]' \
  'Description=Check the Lark ASR worker GPU runtime every minute' \
  '' \
  '[Timer]' \
  'OnBootSec=1min' \
  'OnUnitActiveSec=1min' \
  'AccuracySec=10s' \
  'Persistent=true' \
  '' \
  '[Install]' \
  'WantedBy=timers.target' \
  > "$TIMER_FILE"

systemctl --user daemon-reload
systemctl --user enable --now lark-asr-gpu-watchdog.timer
systemctl --user start lark-asr-gpu-watchdog.service
systemctl --user --no-pager status lark-asr-gpu-watchdog.timer
