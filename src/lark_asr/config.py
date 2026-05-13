from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class PathsConfig:
    state_dir: Path = Path("./data")
    work_dir: Path = Path("./work")
    knowledgebase_dir: Path = Path("/home/xavierx/projects/xfx_knowledge_base")


@dataclass(frozen=True)
class LarkConfig:
    cli: str = "lark-cli"
    profile: str = ""
    event_as: str = "bot"
    api_as: str = "user"
    event_filter: str = r"im\.message|vc\.|calendar\."
    event_types: str = ""
    no_proxy: bool = True
    path_prefixes: tuple[Path, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineConfig:
    transcript_first: bool = True
    local_asr_fallback: bool = True
    resolve_retries: tuple[str, ...] = ("1m", "5m", "15m", "30m")
    minimum_transcript_chars: int = 80
    auto_kb_write: bool = False


@dataclass(frozen=True)
class AsrConfig:
    enabled: bool = False
    command: str = ""
    output_glob: str = "local_asr/**/*.md"


@dataclass(frozen=True)
class CodexConfig:
    enabled: bool = False
    cmd: str = "codex"
    model: str = ""
    reasoning_effort: str = "high"
    extra_args: str = ""
    prompt_template: str = ""


@dataclass(frozen=True)
class ProjectConfig:
    id: str
    path: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    paths: PathsConfig = field(default_factory=PathsConfig)
    lark: LarkConfig = field(default_factory=LarkConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    codex: CodexConfig = field(default_factory=CodexConfig)
    projects: tuple[ProjectConfig, ...] = ()

    @property
    def db_path(self) -> Path:
        return self.paths.state_dir / "lark-asr.sqlite3"

    def ensure_dirs(self) -> None:
        self.paths.state_dir.mkdir(parents=True, exist_ok=True)
        self.paths.work_dir.mkdir(parents=True, exist_ok=True)


def load_config(path: str | Path) -> Config:
    config_path = Path(path).expanduser()
    with config_path.open("rb") as f:
        raw = tomllib.load(f)

    paths = PathsConfig(
        state_dir=_path(raw.get("paths", {}).get("state_dir", "./data")),
        work_dir=_path(raw.get("paths", {}).get("work_dir", "./work")),
        knowledgebase_dir=_path(
            raw.get("paths", {}).get(
                "knowledgebase_dir", "/home/xavierx/projects/xfx_knowledge_base"
            )
        ),
    )
    lark_raw = raw.get("lark", {})
    lark = LarkConfig(
        cli=str(lark_raw.get("cli", "lark-cli")),
        profile=str(lark_raw.get("profile", "")),
        event_as=str(lark_raw.get("event_as", "bot")),
        api_as=str(lark_raw.get("api_as", "user")),
        event_filter=str(lark_raw.get("event_filter", r"im\.message|vc\.|calendar\.")),
        event_types=str(lark_raw.get("event_types", "")),
        no_proxy=bool(lark_raw.get("no_proxy", True)),
        path_prefixes=tuple(_path(item) for item in lark_raw.get("path_prefixes", [])),
        env={str(key): str(value) for key, value in lark_raw.get("env", {}).items()},
    )
    pipeline_raw = raw.get("pipeline", {})
    pipeline = PipelineConfig(
        transcript_first=bool(pipeline_raw.get("transcript_first", True)),
        local_asr_fallback=bool(pipeline_raw.get("local_asr_fallback", True)),
        resolve_retries=tuple(pipeline_raw.get("resolve_retries", ["1m", "5m", "15m", "30m"])),
        minimum_transcript_chars=int(pipeline_raw.get("minimum_transcript_chars", 80)),
        auto_kb_write=bool(pipeline_raw.get("auto_kb_write", False)),
    )
    asr_raw = raw.get("asr", {})
    asr = AsrConfig(
        enabled=bool(asr_raw.get("enabled", False)),
        command=str(asr_raw.get("command", "")),
        output_glob=str(asr_raw.get("output_glob", "local_asr/**/*.md")),
    )
    codex_raw = raw.get("codex", {})
    codex = CodexConfig(
        enabled=bool(codex_raw.get("enabled", False)),
        cmd=str(codex_raw.get("cmd", "codex")),
        model=str(codex_raw.get("model", "")),
        reasoning_effort=str(codex_raw.get("reasoning_effort", "high")),
        extra_args=str(codex_raw.get("extra_args", "")),
        prompt_template=str(codex_raw.get("prompt_template", "")),
    )
    projects = tuple(
        ProjectConfig(
            id=str(item.get("id", "")),
            path=str(item.get("path", "")),
            aliases=tuple(str(alias) for alias in item.get("aliases", [])),
        )
        for item in raw.get("project", [])
    )
    return Config(paths=paths, lark=lark, pipeline=pipeline, asr=asr, codex=codex, projects=projects)


def _path(value: str) -> Path:
    return Path(value).expanduser()
