# host Current State - 2026-05-13

This is a factual snapshot of the current host deployment.

## Running Service

- Host: `host`
- Runtime today: host-run `systemd --user`, not Docker.
- Directory: `/path/to/projects/lark-asr`
- host copy is now a git worktree at `origin/main`.
- Services:
  - `lark-asr-hook.service`: enabled, active
  - `lark-asr-worker.service`: enabled, active
- Hook receives Lark IM events. Recent events without minutes, meeting, or calendar IDs were ignored.

## Current host Config

- `codex.enabled = true`
- `pipeline.auto_kb_write = true`
- `asr.enabled = true`
- Lark CLI auth config: `/path/to/.config/lark-cli`
- Codex home: `/path/to/.codex`
- Knowledgebase: `/path/to/projects/knowledgebase`

## ASR Runtime

Existing ASR work directory:

- `/path/to/models`
- Size: about `13G`
- Python: `/path/to/models/.venv/bin/python`
- Packages:
  - `torch 2.11.0+cu130`
  - `torchaudio 2.11.0+cu130`
  - `funasr 1.3.1`
  - `faster-whisper 1.2.1`
  - `ctranslate2 4.7.1`
  - `modelscope 1.36.3`
- Whisper CT2 model:
  - `/path/to/models/models/AI-ModelScope/whisper-large-v3-ct2-float16/model.bin`

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
- Pulling `python:3.12-slim` from Docker Hub failed with TLS handshake timeout during the initial audit.
- After the Docker restructure, `node:22-bookworm-slim` resolved and was cached, but `docker compose build hook` was still too slow at `apt-get update` and was stopped by a 90 second probe timeout.

## Main Gap

The project has a working host-run MVP and a Docker-first repo layout. Docker production migration is still blocked by host Docker GPU runtime setup and slow external package/image downloads.
