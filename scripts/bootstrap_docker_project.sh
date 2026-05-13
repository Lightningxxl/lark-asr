#!/usr/bin/env bash
set -euo pipefail

ROOT="${LARK_ASR_HOME:-$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"

mkdir -p config data work models secrets

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "created .env from .env.example"
fi

if [[ ! -f config/config.toml ]]; then
  cp config/docker.example.toml config/config.toml
  echo "created config/config.toml from config/docker.example.toml"
fi

echo "next:"
echo "  1. edit .env if FF1 paths differ"
echo "  2. edit config/config.toml, especially pipeline.auto_kb_write"
echo "  3. run ./scripts/docker_doctor.sh"
echo "  4. run docker compose up -d --build"
