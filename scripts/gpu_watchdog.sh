#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${LARK_ASR_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOCK_FILE="${XDG_RUNTIME_DIR:-/tmp}/lark-asr-gpu-watchdog.lock"

exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

cd "$PROJECT_DIR"

if ! nvidia-smi >/dev/null 2>&1; then
  echo "host NVIDIA runtime is unavailable" >&2
  exit 1
fi

worker_id="$(docker compose ps -q worker)"
if [ -n "$worker_id" ] \
  && [ "$(docker inspect --format '{{.State.Running}}' "$worker_id")" = "true" ] \
  && docker exec "$worker_id" nvidia-smi >/dev/null 2>&1; then
  exit 0
fi

echo "worker GPU runtime is unavailable; recreating worker"
docker compose up -d --no-deps --force-recreate worker
worker_id="$(docker compose ps -q worker)"
test -n "$worker_id"
docker exec "$worker_id" nvidia-smi >/dev/null
echo "worker GPU runtime recovered"
