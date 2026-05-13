from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Iterable

from .config import Config
from .events import extract_minute_tokens


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class LarkClient:
    def __init__(self, config: Config):
        self.config = config

    def event_command(self) -> list[str]:
        command = [
            self.config.lark.cli,
            "event",
            "+subscribe",
            "--as",
            self.config.lark.event_as,
            "--compact",
        ]
        if self.config.lark.event_filter:
            command.extend(["--filter", self.config.lark.event_filter])
        if self.config.lark.event_types:
            command.extend(["--event-types", self.config.lark.event_types])
        return self._with_profile(command)

    def recording(self, *, meeting_id: str = "", calendar_event_id: str = "", cwd: Path) -> CommandResult:
        command = [self.config.lark.cli, "vc", "+recording", "--as", self.config.lark.api_as]
        if meeting_id:
            command.extend(["--meeting-ids", meeting_id])
        if calendar_event_id:
            command.extend(["--calendar-event-ids", calendar_event_id])
        command.extend(["--format", "json"])
        return self.run(command, cwd=cwd)

    def notes(self, minute_token: str, output_dir: Path) -> CommandResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        cwd = output_dir.parent.resolve()
        output_arg = output_dir.name
        command = [
            self.config.lark.cli,
            "vc",
            "+notes",
            "--minute-tokens",
            minute_token,
            "--output-dir",
            output_arg,
            "--overwrite",
            "--as",
            self.config.lark.api_as,
            "--format",
            "json",
        ]
        return self.run(command, cwd=cwd)

    def download_media(self, minute_token: str, output_dir: Path) -> CommandResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        cwd = output_dir.parent.resolve()
        output_arg = output_dir.name
        command = [
            self.config.lark.cli,
            "minutes",
            "+download",
            "--minute-tokens",
            minute_token,
            "--output",
            output_arg,
            "--overwrite",
            "--as",
            self.config.lark.api_as,
            "--format",
            "json",
        ]
        return self.run(command, cwd=cwd)

    def run(self, command: list[str], *, cwd: Path) -> CommandResult:
        final_command = self._with_profile(command)
        completed = subprocess.run(
            final_command,
            cwd=cwd,
            env=self.env(),
            text=True,
            capture_output=True,
            check=False,
        )
        return CommandResult(
            args=final_command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.config.lark.no_proxy:
            env["LARK_CLI_NO_PROXY"] = "1"
        if self.config.lark.path_prefixes:
            prefix = os.pathsep.join(str(path) for path in self.config.lark.path_prefixes)
            env["PATH"] = prefix + os.pathsep + env.get("PATH", "")
        env.update(self.config.lark.env)
        return env

    def _with_profile(self, command: list[str]) -> list[str]:
        if self.config.lark.profile and "--profile" not in command:
            return [*command, "--profile", self.config.lark.profile]
        return command


def json_from_stdout(result: CommandResult) -> Any | None:
    if not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def write_command_artifacts(result: CommandResult, directory: Path, stem: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{stem}.stdout").write_text(result.stdout, encoding="utf-8")
    (directory / f"{stem}.stderr").write_text(result.stderr, encoding="utf-8")
    (directory / f"{stem}.args.json").write_text(
        json.dumps(result.args, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def extract_minute_token_from_result(result: CommandResult) -> str:
    data = json_from_stdout(result)
    for value in iter_json_values(data):
        if isinstance(value, str):
            tokens = extract_minute_tokens(value)
            if tokens:
                return sorted(tokens)[0]
    for key, value in iter_json_items(data):
        if normalize_key(key) == "minutetoken" and isinstance(value, str) and value:
            return value
    return ""


def iter_json_values(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for child in value.values():
            yield child
            yield from iter_json_values(child)
    elif isinstance(value, list):
        for child in value:
            yield child
            yield from iter_json_values(child)


def iter_json_items(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from iter_json_items(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json_items(child)


def normalize_key(key: str) -> str:
    return "".join(ch for ch in key.lower() if ch.isalnum())
