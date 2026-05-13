from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from .events import JobSeed
from .timeutil import now_iso


ACTIVE_STATUSES = (
    "queued",
    "waiting_transcript",
    "running_asr",
    "running_codex",
    "needs_asr",
)


@dataclass(frozen=True)
class Job:
    id: str
    source: str
    status: str
    created_at: str
    updated_at: str
    not_before: str
    attempts: int
    retry_index: int
    minute_token: str
    meeting_id: str
    calendar_event_id: str
    project_hint: str
    media_path: str
    transcript_path: str
    event_type: str
    last_error: str
    metadata: dict[str, Any]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Job":
        return cls(
            id=row["id"],
            source=row["source"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            not_before=row["not_before"],
            attempts=row["attempts"],
            retry_index=row["retry_index"],
            minute_token=row["minute_token"] or "",
            meeting_id=row["meeting_id"] or "",
            calendar_event_id=row["calendar_event_id"] or "",
            project_hint=row["project_hint"] or "",
            media_path=row["media_path"] or "",
            transcript_path=row["transcript_path"] or "",
            event_type=row["event_type"] or "",
            last_error=row["last_error"] or "",
            metadata=json.loads(row["metadata_json"] or "{}"),
        )


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        self.conn.close()

    def init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY,
              source TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              not_before TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,
              retry_index INTEGER NOT NULL DEFAULT 0,
              minute_token TEXT NOT NULL DEFAULT '',
              meeting_id TEXT NOT NULL DEFAULT '',
              calendar_event_id TEXT NOT NULL DEFAULT '',
              project_hint TEXT NOT NULL DEFAULT '',
              media_path TEXT NOT NULL DEFAULT '',
              transcript_path TEXT NOT NULL DEFAULT '',
              event_type TEXT NOT NULL DEFAULT '',
              last_error TEXT NOT NULL DEFAULT '',
              metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_status_not_before
              ON jobs(status, not_before);

            CREATE TABLE IF NOT EXISTS logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              level TEXT NOT NULL,
              message TEXT NOT NULL,
              data_json TEXT NOT NULL DEFAULT '{}',
              FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );
            """
        )
        self.conn.commit()

    def enqueue_seed(self, seed: JobSeed) -> Job:
        metadata = dict(seed.metadata or {})
        media_path = str(metadata.pop("media_path", ""))
        timestamp = now_iso()
        existing = self.get(seed.stable_id)
        if existing:
            merged_metadata = {**existing.metadata, **metadata}
            self.conn.execute(
                """
                UPDATE jobs
                SET updated_at = ?,
                    minute_token = COALESCE(NULLIF(?, ''), minute_token),
                    meeting_id = COALESCE(NULLIF(?, ''), meeting_id),
                    calendar_event_id = COALESCE(NULLIF(?, ''), calendar_event_id),
                    project_hint = COALESCE(NULLIF(?, ''), project_hint),
                    media_path = COALESCE(NULLIF(?, ''), media_path),
                    event_type = COALESCE(NULLIF(?, ''), event_type),
                    metadata_json = ?
                WHERE id = ?
                """,
                (
                    timestamp,
                    seed.minute_token,
                    seed.meeting_id,
                    seed.calendar_event_id,
                    seed.project_hint,
                    media_path,
                    seed.event_type,
                    json.dumps(merged_metadata, ensure_ascii=False, sort_keys=True),
                    seed.stable_id,
                ),
            )
            self.conn.commit()
            self.log(seed.stable_id, "info", "job updated from duplicate seed")
            return self.get(seed.stable_id)  # type: ignore[return-value]

        self.conn.execute(
            """
            INSERT INTO jobs (
              id, source, status, created_at, updated_at, not_before,
              minute_token, meeting_id, calendar_event_id, project_hint,
              media_path, event_type, metadata_json
            ) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                seed.stable_id,
                seed.source,
                timestamp,
                timestamp,
                timestamp,
                seed.minute_token,
                seed.meeting_id,
                seed.calendar_event_id,
                seed.project_hint,
                media_path,
                seed.event_type,
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            ),
        )
        self.conn.commit()
        self.log(seed.stable_id, "info", "job queued")
        job = self.get(seed.stable_id)
        assert job is not None
        return job

    def get(self, job_id: str) -> Job | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job.from_row(row) if row else None

    def due_jobs(self, limit: int = 5) -> list[Job]:
        timestamp = now_iso()
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        rows = self.conn.execute(
            f"""
            SELECT * FROM jobs
            WHERE status IN ({placeholders})
              AND not_before <= ?
            ORDER BY not_before, created_at
            LIMIT ?
            """,
            (*ACTIVE_STATUSES, timestamp, limit),
        ).fetchall()
        return [Job.from_row(row) for row in rows]

    def list_jobs(self, limit: int = 20) -> list[Job]:
        rows = self.conn.execute(
            "SELECT * FROM jobs ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [Job.from_row(row) for row in rows]

    def update(
        self,
        job_id: str,
        *,
        status: str | None = None,
        not_before: str | None = None,
        retry_index: int | None = None,
        minute_token: str | None = None,
        meeting_id: str | None = None,
        calendar_event_id: str | None = None,
        project_hint: str | None = None,
        media_path: str | None = None,
        transcript_path: str | None = None,
        last_error: str | None = None,
        metadata: dict[str, Any] | None = None,
        increment_attempts: bool = False,
    ) -> None:
        fields: list[str] = ["updated_at = ?"]
        values: list[Any] = [now_iso()]
        updates = {
            "status": status,
            "not_before": not_before,
            "retry_index": retry_index,
            "minute_token": minute_token,
            "meeting_id": meeting_id,
            "calendar_event_id": calendar_event_id,
            "project_hint": project_hint,
            "media_path": media_path,
            "transcript_path": transcript_path,
            "last_error": last_error,
        }
        for column, value in updates.items():
            if value is not None:
                fields.append(f"{column} = ?")
                values.append(value)
        if metadata is not None:
            fields.append("metadata_json = ?")
            values.append(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
        if increment_attempts:
            fields.append("attempts = attempts + 1")
        values.append(job_id)
        self.conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)
        self.conn.commit()

    def log(self, job_id: str, level: str, message: str, data: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO logs(job_id, created_at, level, message, data_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                job_id,
                now_iso(),
                level,
                message,
                json.dumps(data or {}, ensure_ascii=False, sort_keys=True),
            ),
        )
        self.conn.commit()

    def logs(self, job_id: str, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM logs WHERE job_id = ? ORDER BY id DESC LIMIT ?",
            (job_id, limit),
        ).fetchall()


def render_jobs(jobs: Iterable[Job]) -> str:
    rows = [
        [
            "id",
            "status",
            "minute_token",
            "meeting_id",
            "retry",
            "not_before",
            "transcript",
        ]
    ]
    for job in jobs:
        rows.append(
            [
                job.id,
                job.status,
                job.minute_token,
                job.meeting_id,
                str(job.retry_index),
                job.not_before,
                "yes" if job.transcript_path else "",
            ]
        )
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    return "\n".join(
        "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)) for row in rows
    )

