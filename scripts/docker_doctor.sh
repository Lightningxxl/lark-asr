#!/usr/bin/env bash
set -euo pipefail

ROOT="${LARK_ASR_HOME:-$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"

ok() { printf 'ok %s\n' "$*"; }
warn() { printf 'warn %s\n' "$*" >&2; }
fail() { printf 'fail %s\n' "$*" >&2; }

[[ -f .env ]] && ok ".env exists" || fail ".env missing; run ./scripts/bootstrap_docker_project.sh"
[[ -f config/config.toml ]] && ok "config/config.toml exists" || fail "config/config.toml missing"

set -a
if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  source .env
fi
set +a

for var in LARK_CLI_CONFIG_DIR CODEX_HOME KNOWLEDGEBASE_DIR MODELS_DIR; do
  value="${!var:-}"
  if [[ -z "$value" ]]; then
    fail "$var is not set"
  elif [[ -e "$value" ]]; then
    ok "$var: $value"
  else
    fail "$var path does not exist: $value"
  fi
done

if command -v docker >/dev/null 2>&1; then
  ok "docker: $(docker --version)"
else
  fail "docker not found"
fi

if docker compose version >/dev/null 2>&1; then
  ok "docker compose: $(docker compose version)"
else
  fail "docker compose not available"
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  ok "nvidia-smi available"
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader || true
else
  warn "nvidia-smi not found on host"
fi

if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then
  ok "Docker NVIDIA runtime is configured"
else
  warn "Docker NVIDIA runtime is not configured; worker GPU access will fail until nvidia-container-toolkit is installed and configured"
fi

if command -v nvidia-ctk >/dev/null 2>&1; then
  ok "nvidia-ctk: $(nvidia-ctk --version)"
else
  warn "nvidia-ctk not found"
fi

docker compose config >/tmp/lark-asr-compose.check.yaml
ok "docker compose config rendered at /tmp/lark-asr-compose.check.yaml"
