from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=UTC)
    return datetime.fromisoformat(value)


def parse_duration(value: str) -> timedelta:
    match = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", value)
    if not match:
        raise ValueError(f"Unsupported duration: {value!r}")
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "s":
        return timedelta(seconds=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(days=amount)


def after_duration(value: str) -> str:
    return (datetime.now(UTC) + parse_duration(value)).isoformat(timespec="seconds")

