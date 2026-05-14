# lark-asr

Feishu/Lark transcript-first meeting ingestion for FF1.

The intended flow is:

1. Listen to Feishu events with `lark-cli event +subscribe`.
2. Resolve `minute_token` from a minutes URL, meeting ID, or calendar event ID.
3. Prefer Feishu's generated transcript.
4. If Feishu has media but no transcript, run a local ASR command on FF1.
5. Hand the transcript to Codex inside the knowledgebase repo.

This deliberately uses the presence of transcript/media as the source of truth. It does not guess whether the Feishu quota has been exhausted.

## Quick Start On FF1

The intended managed deployment is Docker Compose:

```bash
git clone https://github.com/Lightningxxl/lark-asr.git
cd lark-asr
./scripts/bootstrap_docker_project.sh
$EDITOR .env
$EDITOR config/config.toml
./scripts/docker_doctor.sh
docker compose up -d --build
docker compose logs -f
```

The Compose deployment uses:

- `hook`: listens to Lark/Feishu events.
- `worker`: resolves transcripts, runs local ASR fallback, and invokes Codex.
- `config/config.toml`: runtime behavior.
- `.env`: host path bindings for auth, knowledgebase, and models.
- `data/`: SQLite state.
- `work/`: job artifacts.

FF1 currently still has a host-run `systemd --user` MVP online. See `docs/ff1-current-state-2026-05-13.md` before migrating it to Docker.

## Host-Run Fallback

Use this only as a fallback while Docker GPU/runtime/network prerequisites are being fixed:

```bash
git clone https://github.com/Lightningxxl/lark-asr.git
cd lark-asr
cp config/ff1-host.example.toml config.toml
$EDITOR config.toml
./bin/lark-asr init --config config.toml
```

On FF1 the sample config assumes:

- `lark-cli`: `/home/xavierx/.local/share/mise/installs/node/22.22.2/bin/lark-cli`
- lark-cli config dir: `/home/xavierx/.config/lark-cli-token-only`
- Codex shim: `/home/xavierx/.local/share/mise/shims/codex`
- Knowledgebase repo: `/home/xavierx/projects/xfx_knowledge_base`
- Node bin path for lark-cli: `/home/xavierx/.local/share/mise/installs/node/22.22.2/bin`

If the knowledgebase repo is not present on FF1 yet, keep `[codex].enabled = false` until it is cloned there.

If `lark-asr doctor` reports auth failure, rerun `lark-cli auth login` for the config dir before starting the hook. The service intentionally does not store app secrets or refresh tokens itself.

Manual smoke test with an existing minutes URL:

```bash
./bin/lark-asr enqueue --config config.toml \
  --minutes-url 'https://gcnb8zkig121.feishu.cn/minutes/obcnlhmgj4929j262r5gy1q5' \
  --project-hint smart-store
./bin/lark-asr worker --config config.toml --once
./bin/lark-asr status --config config.toml
```

Run the hook and worker:

```bash
./bin/lark-asr hook --config config.toml
./bin/lark-asr worker --config config.toml
```

## FF1 User Services

On FF1, the most direct deployment is a pair of user-level systemd services. This keeps using the host-installed `lark-cli`, Codex, lark-cli auth config, and GPU environment from `config.toml`.

```bash
./scripts/install_user_services.sh
systemctl --user status lark-asr-hook lark-asr-worker
journalctl --user -u lark-asr-hook -u lark-asr-worker -f
```

Useful overrides:

```bash
LARK_ASR_CONFIG=/home/xavierx/projects/lark-asr/config.toml \
LARK_ASR_WORKER_INTERVAL=20 \
./scripts/install_user_services.sh
```

To remove the services:

```bash
./scripts/uninstall_user_services.sh
```

If services should survive after the SSH login session is gone or after reboot, enable user lingering once on FF1:

```bash
sudo loginctl enable-linger xavierx
```

## Docker Compose

The compose file expects only explicit host path bindings from `.env`:

- `LARK_CLI_CONFIG_DIR`
- `CODEX_HOME`
- `SSH_DIR`
- `KNOWLEDGEBASE_DIR`
- `MODELS_DIR`

It does not mount host-installed `lark-cli`, Codex, or Python virtualenvs. The images own those runtimes.

```bash
./scripts/bootstrap_docker_project.sh
./scripts/docker_doctor.sh
docker compose up -d --build
docker compose logs -f
```

For GPU ASR fallback, set `[asr].enabled = true` and point `[asr].command` at the FF1 transcription script. The command receives these placeholders:

- `{media_path}`
- `{job_dir}`
- `{minute_token}`
- `{meeting_id}`
- `{calendar_event_id}`
- `{project_hint}`
- `{knowledgebase_dir}`

The command should write a Markdown transcript under `{job_dir}`. The worker searches `[asr].output_glob`.

When running under Docker Compose, use `/app/scripts/asr_fallback.sh` for the bundled ASR command path. The Docker config uses `/models/whisper-large-v3-ct2-float16`, so `MODELS_DIR` should point to the directory containing that model folder.

The repo includes `scripts/asr_fallback.sh`, which uses bundled helper scripts:

- `transcribe_funasr.py` for FunASR/SenseVoice + VAD + punctuation + CAM++ speaker labels.
- `transcribe_faster_whisper.py` for Whisper large-v3 text when `faster-whisper` is available.
- `label_whisper_with_speakers.py` to combine Whisper text with FunASR speaker segments.

Useful environment knobs for the ASR command:

- `LARK_ASR_PYTHON`
- `LARK_ASR_WHISPER_MODEL`
- `LARK_ASR_WHISPER_MODEL_DIR`
- `LARK_ASR_USE_WHISPER`
- `LARK_ASR_DEVICE`
- `LARK_ASR_FUNASR_DEVICE`

The current FF1 host has an existing ASR environment at `/home/xavierx/codex-transcript-20260512/.venv/bin/python` and a local faster-whisper CT2 model at `/home/xavierx/codex-transcript-20260512/models/AI-ModelScope/whisper-large-v3-ct2-float16`. Docker should reuse the model files via `MODELS_DIR`, not mount the old virtualenv.

See `docs/docker-runbook.md` for the Docker migration sequence and current FF1 blockers.

## Codex Step

Set `[codex].enabled = true` after confirming the transcript path looks right.

With `auto_kb_write = false`, Codex runs in read-only mode and produces an import plan. With `auto_kb_write = true`, Codex can edit the knowledgebase repo directly.

The prompt is intentionally short and tells Codex to read `AGENTS.md` and local context instead of encoding knowledgebase conventions in this service. The knowledgebase path should be a real git repo; successful write runs are expected to self-check, commit, and attempt `git push origin main`. Mount `SSH_DIR` so the worker has the GitHub key needed for that push; the compose file binds it to both the Codex `HOME` path and the container user's OpenSSH path.

## Useful Commands

```bash
./bin/lark-asr status --config config.toml
./bin/lark-asr logs minute:obcnlhmgj4929j262r5gy1q5 --config config.toml
./bin/lark-asr worker --config config.toml --once
```
