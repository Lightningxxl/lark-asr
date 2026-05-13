from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable

from .config import ProjectConfig


MINUTES_URL_RE = re.compile(r"https?://[^\s\"'<>]+/minutes/([A-Za-z0-9_-]+)")
TOKEN_RE = re.compile(r"\b(?:minute_token|minuteToken)\b[\"':=\s]+([A-Za-z0-9_-]{8,})")


@dataclass(frozen=True)
class JobSeed:
    source: str
    minute_token: str = ""
    meeting_id: str = ""
    calendar_event_id: str = ""
    project_hint: str = ""
    event_type: str = ""
    metadata: dict[str, Any] | None = None

    @property
    def stable_id(self) -> str:
        if self.minute_token:
            return f"minute:{self.minute_token}"
        if self.meeting_id:
            return f"meeting:{self.meeting_id}"
        if self.calendar_event_id:
            return f"calendar:{self.calendar_event_id}"
        digest = hashlib.sha1(
            json.dumps(self.metadata or {}, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        return f"event:{digest}"


def seeds_from_event(event: dict[str, Any], projects: Iterable[ProjectConfig] = ()) -> list[JobSeed]:
    event_type = get_event_type(event)
    strings = list(iter_strings(event))
    minute_tokens = set()
    meeting_ids = set()
    calendar_event_ids = set()

    for value in strings:
        minute_tokens.update(extract_minute_tokens(value))

    for key, value in iter_key_values(event):
        normalized = normalize_key(key)
        if isinstance(value, str):
            if normalized in {"minutetoken", "minuteurltoken"}:
                minute_tokens.add(value)
            elif normalized == "meetingid":
                meeting_ids.add(value)
            elif normalized in {"calendareventid", "calendareventinstanceid"}:
                calendar_event_ids.add(value)

    project_hint = infer_project_hint(" ".join(strings), projects)
    seeds: list[JobSeed] = []
    for token in sorted(minute_tokens):
        seeds.append(
            JobSeed(
                source="event",
                minute_token=token,
                project_hint=project_hint,
                event_type=event_type,
                metadata=event,
            )
        )

    if not seeds:
        for meeting_id in sorted(meeting_ids):
            seeds.append(
                JobSeed(
                    source="event",
                    meeting_id=meeting_id,
                    project_hint=project_hint,
                    event_type=event_type,
                    metadata=event,
                )
            )

    if not seeds:
        for calendar_event_id in sorted(calendar_event_ids):
            seeds.append(
                JobSeed(
                    source="event",
                    calendar_event_id=calendar_event_id,
                    project_hint=project_hint,
                    event_type=event_type,
                    metadata=event,
                )
            )
    return seeds


def seed_from_manual(
    *,
    minute_token: str = "",
    meeting_id: str = "",
    calendar_event_id: str = "",
    project_hint: str = "",
    media_path: str = "",
) -> JobSeed:
    metadata: dict[str, Any] = {}
    if media_path:
        metadata["media_path"] = media_path
    return JobSeed(
        source="manual",
        minute_token=minute_token,
        meeting_id=meeting_id,
        calendar_event_id=calendar_event_id,
        project_hint=project_hint,
        metadata=metadata,
    )


def get_event_type(event: dict[str, Any]) -> str:
    header = event.get("header")
    if isinstance(header, dict):
        value = header.get("event_type") or header.get("eventType")
        if isinstance(value, str):
            return value
    for key in ("event_type", "eventType", "schema"):
        value = event.get(key)
        if isinstance(value, str):
            return value
    return ""


def extract_minute_tokens(text: str) -> set[str]:
    tokens = set(match.group(1).rstrip(").,，。]】") for match in MINUTES_URL_RE.finditer(text))
    tokens.update(match.group(1).rstrip(").,，。]】") for match in TOKEN_RE.finditer(text))
    return tokens


def infer_project_hint(text: str, projects: Iterable[ProjectConfig]) -> str:
    for project in projects:
        if project.id and project.id in text:
            return project.id
        for alias in project.aliases:
            if alias and alias in text:
                return project.id
    return ""


def iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from iter_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_strings(child)


def iter_key_values(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from iter_key_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_key_values(child)


def normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())

