# Docker Runbook

## Goal

The desired deployment is:

```bash
cp .env.example .env
./scripts/bootstrap_docker_project.sh
docker compose up -d --build
```

The containers should own the application runtime. Host paths are only for:

- Lark CLI auth state
- Lark CLI encrypted secret data
- Codex auth/config state
- knowledgebase repository
- model cache
- persistent `data/` and `work/`

## Layout

- `compose.yaml`: Docker-first hook and worker services.
- `Dockerfile`: lightweight app image with Python, `lark-cli`, and Codex CLI.
- `docker/Dockerfile.asr`: GPU worker image with app runtime plus ASR dependencies. It defaults to the same Debian/Node base as the hook image and uses CUDA-enabled Python wheels to avoid pulling a large NVIDIA CUDA base image on FF1's slow Docker Hub path.
- `config/docker.example.toml`: container paths and commands.
- `.env.example`: FF1 path bindings and image pins.
- `scripts/bootstrap_docker_project.sh`: creates local config/data/work folders.
- `scripts/docker_doctor.sh`: checks Docker, GPU runtime, path bindings, and compose config.

## FF1 Prerequisites

1. Docker and Docker Compose.
2. NVIDIA Container Toolkit configured for Docker.
3. Reachable image registries or a configured Docker registry mirror/proxy.
4. Existing auth directories:
   - `LARK_CLI_CONFIG_DIR=/home/xavierx/.config/lark-cli-token-only`
   - `LARK_CLI_DATA_DIR=/home/xavierx/.local/share`
   - `CODEX_HOME=/home/xavierx/.codex`
   - `SSH_DIR=/home/xavierx/.ssh`
5. Existing knowledgebase:
   - `KNOWLEDGEBASE_DIR=/home/xavierx/projects/xfx_knowledge_base`
6. Existing model directory or a populated project `models/` directory:
   - `MODELS_DIR=/home/xavierx/codex-transcript-20260512/models/AI-ModelScope`

## NVIDIA Runtime

The FF1 audit found that Docker does not currently expose the NVIDIA runtime. Per NVIDIA's official container toolkit installation guide, the usual Ubuntu/Debian path is:

```bash
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Then verify the runtime is registered:

```bash
docker info | grep -i nvidia
```

Reference: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html

## Known FF1 Blockers

- External image/package downloads are slow or unreliable. Keep build mirrors configurable in `.env`; avoid NVIDIA CUDA base images unless the network path is fixed.
- The Docker worker still needs one successful GPU smoke test after the image finishes building.

## Migration Sequence

1. Keep host-run services running.
2. On FF1, clone this repo as a real git worktree.
3. Run:

   ```bash
   ./scripts/bootstrap_docker_project.sh
   ./scripts/docker_doctor.sh
   docker compose build hook
   docker compose build worker
   ```

4. Run a manual job in the Docker worker against a known minutes URL.
5. Stop host-run systemd services.
6. Start Docker services:

   ```bash
   docker compose up -d
   docker compose logs -f
   ```

7. Keep the host-run config as rollback until at least one transcript-first and one local-ASR fallback job succeed.
