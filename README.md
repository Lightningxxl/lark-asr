# lark-asr

Feishu/Lark transcript-first meeting ingestion for host.

The intended flow is:

1. Listen to Feishu events with `lark-cli event +subscribe`.
2. Backfill recent Feishu minutes with `lark-cli minutes +search --as user`, because event subscription is bot-only and can miss user-owned recordings.
3. Resolve `minute_token` from a minutes URL, meeting ID, calendar event ID, or minutes search result.
4. Prefer Feishu's generated transcript.
5. If Feishu has media but no usable transcript, run a local ASR command on host.
6. Hand the transcript to Codex inside the knowledgebase repo.

This deliberately uses transcript/media artifacts as the source of truth. When Feishu returns a transcript, the worker checks whether the last transcript timestamp covers enough of the declared or probed media duration. If the transcript appears partial, it downloads the media and falls back to local ASR. If you already know the Feishu quota is exhausted, set `force_local_asr = true` under `[pipeline]` to skip Feishu transcript import and transcribe locally.

## Quick Start On host

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

- `hook`: listens to Lark/Feishu bot events and periodically backfills recent user-visible minutes.
- `worker`: resolves transcripts, runs local ASR fallback, and invokes Codex.
- `config/config.toml`: runtime behavior.
- `.env`: host path bindings for auth, knowledgebase, and models.
- `data/`: SQLite state.
- `work/`: job artifacts.

host currently still has a host-run `systemd --user` MVP online. See `docs/host-current-state-2026-05-13.md` before migrating it to Docker.

## Host-Run Fallback

Use this only as a fallback while Docker GPU/runtime/network prerequisites are being fixed:

```bash
git clone https://github.com/Lightningxxl/lark-asr.git
cd lark-asr
cp config/host-host.example.toml config.toml
$EDITOR config.toml
./bin/lark-asr init --config config.toml
```

On host the sample config assumes:

- `lark-cli`: `/path/to/.local/share/mise/installs/node/22.22.2/bin/lark-cli`
- lark-cli config dir: `/path/to/.config/lark-cli`
- Codex shim: `/path/to/.local/share/mise/shims/codex`
- Knowledgebase repo: `/path/to/projects/knowledgebase`
- Node bin path for lark-cli: `/path/to/.local/share/mise/installs/node/22.22.2/bin`

If the knowledgebase repo is not present on host yet, keep `[codex].enabled = false` until it is cloned there.

If `lark-asr doctor` reports auth failure, rerun `lark-cli auth login` for the config dir before starting the hook. The service intentionally does not store app secrets or refresh tokens itself.

Manual smoke test with an existing minutes URL:

```bash
./bin/lark-asr enqueue --config config.toml \
  --minutes-url 'https://example.feishu.cn/minutes/obcn00000000000000000000' \
  --project-hint demo-project
./bin/lark-asr worker --config config.toml --once
./bin/lark-asr status --config config.toml
```

Run the hook and worker:

```bash
./bin/lark-asr hook --config config.toml
./bin/lark-asr worker --config config.toml
```

## host User Services

On host, the most direct deployment is a pair of user-level systemd services. This keeps using the host-installed `lark-cli`, Codex, lark-cli auth config, and GPU environment from `config.toml`.

```bash
./scripts/install_user_services.sh
systemctl --user status lark-asr-hook lark-asr-worker
journalctl --user -u lark-asr-hook -u lark-asr-worker -f
```

Useful overrides:

```bash
LARK_ASR_CONFIG=/path/to/projects/lark-asr/config.toml \
LARK_ASR_WORKER_INTERVAL=20 \
./scripts/install_user_services.sh
```

To remove the services:

```bash
./scripts/uninstall_user_services.sh
```

If services should survive after the SSH login session is gone or after reboot, enable user lingering once on host:

```bash
sudo loginctl enable-linger user
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

For GPU ASR fallback, set `[asr].enabled = true` and point `[asr].command` at the host transcription script. The command receives these placeholders:

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
- `label_whisper_with_speakers.py` to preserve Whisper segments and assign FunASR speaker labels.
- `restore_punctuation_funasr.py` to restore punctuation on the final speaker-labeled Whisper transcript.

Useful environment knobs for the ASR command:

- `LARK_ASR_PYTHON`
- `LARK_ASR_WHISPER_MODEL`
- `LARK_ASR_WHISPER_MODEL_DIR`
- `LARK_ASR_USE_WHISPER`
- `LARK_ASR_RESTORE_PUNCTUATION`
- `LARK_ASR_DEVICE`
- `LARK_ASR_FUNASR_MODEL`
- `LARK_ASR_FUNASR_DEVICE`

The current host host has an existing ASR environment at `/path/to/models/.venv/bin/python` and a local faster-whisper CT2 model at `/path/to/models/models/AI-ModelScope/whisper-large-v3-ct2-float16`. Docker should reuse the model files via `MODELS_DIR`, not mount the old virtualenv.

See `docs/docker-runbook.md` for the Docker migration sequence and current host blockers.

## Codex Step

Set `[codex].enabled = true` after confirming the transcript path looks right.

With `auto_kb_write = false`, Codex runs in read-only mode and produces an import plan. With `auto_kb_write = true`, Codex can edit the knowledgebase repo directly.

The prompt is intentionally short and tells Codex to read `AGENTS.md` and local context instead of encoding knowledgebase conventions in this service. The knowledgebase path should be a real git repo. For write runs, `lark-asr` now treats Git publication as part of the pipeline: it syncs the knowledgebase before Codex runs, lets Codex edit and commit, then commits any remaining Codex changes, rebases on `origin/<branch>`, and pushes. A push or rebase failure leaves the job failed with git artifacts under the job `codex/git/` directory. Mount `SSH_DIR` so the worker has the GitHub key needed for that push; the compose file binds it to both the Codex `HOME` path and the container user's OpenSSH path.

## Useful Commands

```bash
./bin/lark-asr status --config config.toml
./bin/lark-asr logs minute:obcn00000000000000000000 --config config.toml
./bin/lark-asr worker --config config.toml --once
```
