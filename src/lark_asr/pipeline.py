from __future__ import annotations

from collections import OrderedDict
import json
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any

from .config import Config
from .lark import (
    LarkClient,
    extract_minute_token_from_result,
    iter_json_items,
    json_from_stdout,
    write_command_artifacts,
)
from .store import Job, Store
from .timeutil import after_duration, now_iso


TEXT_EXTENSIONS = {".md", ".txt", ".srt", ".vtt"}
MEDIA_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".mp4", ".mov", ".mkv"}


class Pipeline:
    def __init__(self, config: Config, store: Store):
        self.config = config
        self.store = store
        self.lark = LarkClient(config)

    def process_due_once(self, limit: int = 5) -> int:
        jobs = self.store.due_jobs(limit=limit)
        for job in jobs:
            self.process_job(job)
        return len(jobs)

    def process_job(self, job: Job) -> None:
        self.store.update(job.id, increment_attempts=True, last_error="")
        job = self.store.get(job.id) or job
        try:
            self._process_job(job)
        except Exception as exc:  # noqa: BLE001 - keep worker alive around one bad job.
            self.store.update(job.id, status="failed", last_error=str(exc))
            self.store.log(job.id, "error", "job failed with unhandled exception", {"error": str(exc)})

    def _process_job(self, job: Job) -> None:
        job_dir = self.job_dir(job)
        job_dir.mkdir(parents=True, exist_ok=True)
        self.store.log(job.id, "info", "processing job", {"status": job.status})

        if not job.minute_token and (job.meeting_id or job.calendar_event_id):
            job = self.resolve_minute_token(job, job_dir)

        if job.transcript_path and Path(job.transcript_path).exists():
            self.run_codex_or_complete(job, Path(job.transcript_path))
            return

        if self.config.pipeline.transcript_first and job.minute_token:
            transcript = self.fetch_feishu_transcript(job, job_dir)
            if transcript:
                self.store.update(job.id, transcript_path=str(transcript))
                job = self.store.get(job.id) or job
                self.run_codex_or_complete(job, transcript)
                return
            if self.should_wait_for_transcript(job):
                self.schedule_transcript_retry(job)
                return

        if self.config.pipeline.local_asr_fallback:
            media = Path(job.media_path) if job.media_path else self.download_media(job, job_dir)
            if media and media.exists():
                self.store.update(job.id, media_path=str(media))
                transcript = self.run_asr(job, media, job_dir)
                if transcript:
                    self.store.update(job.id, transcript_path=str(transcript))
                    job = self.store.get(job.id) or job
                    self.run_codex_or_complete(job, transcript)
                    return
                return

        self.store.update(job.id, status="needs_audio", last_error="no transcript or downloadable media")
        self.store.log(job.id, "warning", "job needs audio")

    def resolve_minute_token(self, job: Job, job_dir: Path) -> Job:
        result = self.lark.recording(
            meeting_id=job.meeting_id,
            calendar_event_id=job.calendar_event_id,
            cwd=job_dir,
        )
        write_command_artifacts(result, job_dir / "recording", "recording")
        token = extract_minute_token_from_result(result)
        if token:
            self.store.update(job.id, minute_token=token)
            self.store.log(job.id, "info", "resolved minute token", {"minute_token": token})
            return self.store.get(job.id) or job
        self.store.log(
            job.id,
            "warning",
            "minute token not resolved",
            {"returncode": result.returncode, "stderr": result.stderr[-1000:]},
        )
        return job

    def fetch_feishu_transcript(self, job: Job, job_dir: Path) -> Path | None:
        notes_dir = job_dir / "feishu_notes"
        result = self.lark.notes(job.minute_token, notes_dir)
        write_command_artifacts(result, notes_dir, "notes")
        transcript = find_best_text_file(notes_dir, self.config.pipeline.minimum_transcript_chars)
        if transcript:
            self.store.log(job.id, "info", "found Feishu transcript artifact", {"path": str(transcript)})
            return transcript

        extracted = extract_transcript_from_json_artifacts(
            notes_dir,
            json_from_stdout(result),
            self.config.pipeline.minimum_transcript_chars,
        )
        if extracted:
            self.store.log(job.id, "info", "extracted Feishu transcript from JSON", {"path": str(extracted)})
            return extracted

        self.store.log(
            job.id,
            "info",
            "Feishu transcript not available yet",
            {"returncode": result.returncode, "stderr": result.stderr[-1000:]},
        )
        return None

    def should_wait_for_transcript(self, job: Job) -> bool:
        return job.retry_index < len(self.config.pipeline.resolve_retries)

    def schedule_transcript_retry(self, job: Job) -> None:
        delay = self.config.pipeline.resolve_retries[job.retry_index]
        self.store.update(
            job.id,
            status="waiting_transcript",
            not_before=after_duration(delay),
            retry_index=job.retry_index + 1,
        )
        self.store.log(job.id, "info", "scheduled transcript retry", {"delay": delay})

    def download_media(self, job: Job, job_dir: Path) -> Path | None:
        if not job.minute_token:
            return None
        media_dir = job_dir / "media"
        result = self.lark.download_media(job.minute_token, media_dir)
        write_command_artifacts(result, media_dir, "download")
        media = find_media_file(media_dir)
        if media:
            self.store.log(job.id, "info", "downloaded media", {"path": str(media)})
            return media
        self.store.log(
            job.id,
            "warning",
            "media download did not produce a media file",
            {"returncode": result.returncode, "stderr": result.stderr[-1000:]},
        )
        return None

    def run_asr(self, job: Job, media_path: Path, job_dir: Path) -> Path | None:
        if not self.config.asr.enabled or not self.config.asr.command.strip():
            self.store.update(job.id, status="needs_asr", last_error="ASR command is not enabled")
            self.store.log(job.id, "warning", "ASR fallback is needed but disabled")
            return None

        self.store.update(job.id, status="running_asr")
        command = format_template(
            self.config.asr.command,
            job=job,
            job_dir=job_dir,
            media_path=media_path,
            transcript_path="",
            config=self.config,
        )
        asr_dir = job_dir / "asr"
        asr_dir.mkdir(parents=True, exist_ok=True)
        (asr_dir / "command.sh").write_text(command, encoding="utf-8")
        completed = subprocess.run(
            command,
            shell=True,
            cwd=job_dir,
            env=self.lark.env(),
            text=True,
            capture_output=True,
            check=False,
        )
        (asr_dir / "stdout.log").write_text(completed.stdout, encoding="utf-8")
        (asr_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode != 0:
            self.store.update(
                job.id,
                status="failed",
                last_error=f"ASR command failed with exit code {completed.returncode}",
            )
            self.store.log(
                job.id,
                "error",
                "ASR command failed",
                {"returncode": completed.returncode, "stderr": completed.stderr[-1000:]},
            )
            return None
        transcript = find_best_text_file(
            job_dir,
            self.config.pipeline.minimum_transcript_chars,
            pattern=self.config.asr.output_glob,
        )
        if not transcript:
            self.store.update(job.id, status="failed", last_error="ASR command produced no transcript")
            self.store.log(job.id, "error", "ASR produced no transcript")
            return None
        self.store.log(job.id, "info", "ASR transcript produced", {"path": str(transcript)})
        return transcript

    def run_codex_or_complete(self, job: Job, transcript_path: Path) -> None:
        if not self.config.codex.enabled:
            self.store.update(job.id, status="completed", transcript_path=str(transcript_path))
            self.store.log(job.id, "info", "completed without Codex step")
            return

        self.store.update(job.id, status="running_codex")
        job_dir = self.job_dir(job)
        prompt = format_template(
            self.config.codex.prompt_template,
            job=job,
            job_dir=job_dir,
            media_path=Path(job.media_path) if job.media_path else Path(""),
            transcript_path=transcript_path,
            config=self.config,
        )
        if not self.config.pipeline.auto_kb_write:
            prompt += (
                "\n\n当前配置 auto_kb_write=false。请只输出计划和建议修改，"
                "不要写入或修改知识库文件。"
            )
        codex_dir = job_dir / "codex"
        codex_dir.mkdir(parents=True, exist_ok=True)
        (codex_dir / "prompt.md").write_text(prompt, encoding="utf-8")

        sandbox = "danger-full-access" if self.config.pipeline.auto_kb_write else "read-only"
        command = [
            self.config.codex.cmd,
            "exec",
            "-C",
            str(self.config.paths.knowledgebase_dir),
            "--add-dir",
            str(job_dir),
            "--skip-git-repo-check",
        ]
        if self.config.pipeline.auto_kb_write:
            command.append("--full-auto")
        else:
            command.extend(["--sandbox", sandbox])
        if self.config.codex.model:
            command.extend(["-m", self.config.codex.model])
        if self.config.codex.reasoning_effort:
            command.extend(["-c", f"model_reasoning_effort={self.config.codex.reasoning_effort}"])
        if self.config.codex.extra_args:
            command.extend(shlex.split(self.config.codex.extra_args))
        command.append(prompt)

        completed = subprocess.run(
            command,
            cwd=self.config.paths.knowledgebase_dir,
            env=self.lark.env(),
            text=True,
            capture_output=True,
            check=False,
        )
        (codex_dir / "stdout.log").write_text(completed.stdout, encoding="utf-8")
        (codex_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
        (codex_dir / "command.json").write_text(
            json.dumps(command, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if completed.returncode != 0:
            self.store.update(
                job.id,
                status="failed",
                last_error=f"Codex command failed with exit code {completed.returncode}",
            )
            self.store.log(
                job.id,
                "error",
                "Codex command failed",
                {"returncode": completed.returncode, "stderr": completed.stderr[-1000:]},
            )
            return
        self.store.update(job.id, status="completed", transcript_path=str(transcript_path))
        self.store.log(job.id, "info", "completed Codex step")

    def job_dir(self, job: Job) -> Path:
        return self.config.paths.work_dir / sanitize_filename(job.id)


def find_best_text_file(base: Path, minimum_chars: int, pattern: str = "**/*") -> Path | None:
    candidates: list[tuple[int, float, Path]] = []
    for path in base.glob(pattern):
        if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        score = len(re.sub(r"\s+", "", text))
        if score >= minimum_chars:
            candidates.append((score, path.stat().st_mtime, path))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def find_media_file(base: Path) -> Path | None:
    candidates = [
        path
        for path in base.rglob("*")
        if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS and path.stat().st_size > 0
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0]


def extract_transcript_from_json_artifacts(
    notes_dir: Path,
    stdout_json: Any | None,
    minimum_chars: int,
) -> Path | None:
    documents: list[Any] = []
    if stdout_json is not None:
        documents.append(stdout_json)
    for path in notes_dir.rglob("*.json"):
        try:
            documents.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue

    chunks: OrderedDict[str, None] = OrderedDict()
    for document in documents:
        for key, value in iter_json_items(document):
            if not isinstance(value, str):
                continue
            normalized = "".join(ch for ch in key.lower() if ch.isalnum())
            if normalized in {"text", "content", "sentence", "summary", "title"} or len(value) > 40:
                cleaned = value.strip()
                if cleaned:
                    chunks[cleaned] = None

    text = "\n\n".join(chunks.keys())
    if len(re.sub(r"\s+", "", text)) < minimum_chars:
        return None
    output = notes_dir / "feishu_transcript.extracted.md"
    output.write_text(text + "\n", encoding="utf-8")
    return output


def format_template(
    template: str,
    *,
    job: Job,
    job_dir: Path,
    media_path: Path,
    transcript_path: Path | str,
    config: Config,
) -> str:
    values = {
        "job_id": job.id,
        "job_dir": str(job_dir),
        "minute_token": job.minute_token,
        "meeting_id": job.meeting_id,
        "calendar_event_id": job.calendar_event_id,
        "project_hint": job.project_hint,
        "media_path": str(media_path),
        "transcript_path": str(transcript_path),
        "knowledgebase_dir": str(config.paths.knowledgebase_dir),
        "now": now_iso(),
    }
    return template.format_map(DefaultDict(values))


def sanitize_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "job"


class DefaultDict(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""
