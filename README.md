# lark-asr

Feishu/Lark transcript-first meeting ingestion for host.

The intended flow is:

1. Listen to Feishu events with `lark-cli event +subscribe`.
2. Resolve `minute_token` from a minutes URL, meeting ID, or calendar event ID.
3. Prefer Feishu's generated transcript.
4. If Feishu has media but no transcript, run a local ASR command on host.
5. Hand the transcript to Codex inside the knowledgebase repo.

This deliberately uses the presence of transcript/media as the source of truth. It does not guess whether the Feishu quota has been exhausted.

## Quick Start On host

```bash
git clone https://github.com/Lightningxxl/lark-asr.git
cd lark-asr
cp config.example.toml config.toml
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

## Docker Compose

The compose file expects host host paths such as `/path/to/.local`, `/path/to/.config`, and `/path/to/.codex` to exist because the first version calls host-installed `lark-cli` and `codex`.

```bash
cp config.example.toml config.toml
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

## Codex Step

Set `[codex].enabled = true` after confirming the transcript path looks right.

With `auto_kb_write = false`, Codex runs in read-only mode and produces an import plan. With `auto_kb_write = true`, Codex can edit the knowledgebase repo directly.

The prompt is intentionally short and tells Codex to read `AGENTS.md` and local context instead of encoding knowledgebase conventions in this service.

## Useful Commands

```bash
./bin/lark-asr status --config config.toml
./bin/lark-asr logs minute:obcn00000000000000000000 --config config.toml
./bin/lark-asr worker --config config.toml --once
```
