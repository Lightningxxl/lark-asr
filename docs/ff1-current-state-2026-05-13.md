# FF1 Current State - 2026-05-13

This is a factual snapshot of the current FF1 deployment.

## Running Service

- Host: `ff1`
- Runtime today: host-run `systemd --user`, not Docker.
- Directory: `/home/xavierx/projects/lark-asr`
- FF1 copy is not currently a git worktree; it was synchronized by tar.
- Services:
  - `lark-asr-hook.service`: enabled, active
  - `lark-asr-worker.service`: enabled, active
- Hook receives Lark IM events. Recent events without minutes, meeting, or calendar IDs were ignored.

## Current FF1 Config

- `codex.enabled = true`
- `pipeline.auto_kb_write = true`
- `asr.enabled = true`
- Lark CLI auth config: `/home/xavierx/.config/lark-cli-token-only`
- Codex home: `/home/xavierx/.codex`
- Knowledgebase: `/home/xavierx/projects/xfx_knowledge_base`

## ASR Runtime

Existing ASR work directory:

- `/home/xavierx/codex-transcript-20260512`
- Size: about `13G`
- Python: `/home/xavierx/codex-transcript-20260512/.venv/bin/python`
- Packages:
  - `torch 2.11.0+cu130`
  - `torchaudio 2.11.0+cu130`
  - `funasr 1.3.1`
  - `faster-whisper 1.2.1`
  - `ctranslate2 4.7.1`
  - `modelscope 1.36.3`
- Whisper CT2 model:
  - `/home/xavierx/codex-transcript-20260512/models/AI-ModelScope/whisper-large-v3-ct2-float16/model.bin`

## GPU

- GPU: NVIDIA GeForce RTX 4090
- Driver: `580.126.09`
- CUDA reported by driver: `13.0`
- Host Python ASR can load CUDA.

## Docker State

- Docker is installed.
- Docker Compose is installed.
- Docker NVIDIA runtime is not configured. `docker info` lists `runc`, but not `nvidia`.
- `nvidia-ctk` was not found.
- Pulling `python:3.12-slim` from Docker Hub failed with TLS handshake timeout during audit.

## Main Gap

The project has a working host-run MVP, but not a clean Docker-managed deployment yet. The Docker work needs to make runtime, config, secrets, model mounts, and GPU prerequisites explicit.
