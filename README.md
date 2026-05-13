# lark-asr

Feishu/Lark transcript-first meeting ingestion for FF1.

The intended flow is:

1. Listen to Feishu events with `lark-cli event +subscribe`.
2. Resolve `minute_token` from a minutes URL, meeting ID, or calendar event ID.
3. Prefer Feishu's generated transcript.
4. If Feishu has media but no transcript, run a local ASR command on FF1.
5. Hand the transcript to Codex inside the knowledgebase repo so it can apply the repo's own rules.

This deliberately uses the presence of transcript/media as the source of truth. It does not guess whether the Feishu quota has been exhausted.

## Quick Start On FF1

```bash
git clone https://github.com/Lightningxxl/lark-asr.git
cd lark-asr
cp config.example.toml config.toml
$EDITOR config.toml
./bin/lark-asr init --config config.toml
```

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

## Docker Compose

The compose file expects FF1 host paths such as `/home/xavierx/.local`, `/home/xavierx/.config`, and `/home/xavierx/.codex` to exist because the first version calls host-installed `lark-cli` and `codex`.

```bash
cp config.example.toml config.toml
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

## Codex Step

Set `[codex].enabled = true` after confirming the transcript path looks right.

With `auto_kb_write = false`, Codex runs in read-only mode and produces an import plan. With `auto_kb_write = true`, Codex can edit the knowledgebase repo directly.

The prompt is intentionally short and tells Codex to read `AGENTS.md` and local context instead of encoding knowledgebase rules in this service.

## Useful Commands

```bash
./bin/lark-asr status --config config.toml
./bin/lark-asr logs minute:obcnlhmgj4929j262r5gy1q5 --config config.toml
./bin/lark-asr worker --config config.toml --once
```
