from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from .config import Config, load_config
from .events import extract_minute_tokens, seed_from_manual, seeds_from_event
from .lark import LarkClient
from .pipeline import Pipeline
from .store import Store, render_jobs


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        raise SystemExit(2)
    args.func(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lark-asr")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Initialize state directories and SQLite schema.")
    add_config(init_parser)
    init_parser.set_defaults(func=init_command)

    enqueue_parser = subparsers.add_parser("enqueue", help="Manually enqueue one job.")
    add_config(enqueue_parser)
    enqueue_parser.add_argument("--minutes-url", default="", help="Feishu minutes URL.")
    enqueue_parser.add_argument("--minute-token", default="", help="Feishu minute token.")
    enqueue_parser.add_argument("--meeting-id", default="", help="Feishu VC meeting ID.")
    enqueue_parser.add_argument("--calendar-event-id", default="", help="Feishu calendar event instance ID.")
    enqueue_parser.add_argument("--media-path", default="", help="Local audio/video path.")
    enqueue_parser.add_argument("--project-hint", default="", help="Project alias or ID.")
    enqueue_parser.set_defaults(func=enqueue_command)

    hook_parser = subparsers.add_parser("hook", help="Subscribe to Feishu events and enqueue jobs.")
    add_config(hook_parser)
    hook_parser.add_argument("--stdin", action="store_true", help="Read NDJSON events from stdin.")
    hook_parser.set_defaults(func=hook_command)

    worker_parser = subparsers.add_parser("worker", help="Process queued jobs.")
    add_config(worker_parser)
    worker_parser.add_argument("--once", action="store_true", help="Process currently due jobs once.")
    worker_parser.add_argument("--interval", type=int, default=20, help="Loop interval in seconds.")
    worker_parser.add_argument("--limit", type=int, default=5, help="Max jobs per tick.")
    worker_parser.set_defaults(func=worker_command)

    status_parser = subparsers.add_parser("status", help="Show recent jobs.")
    add_config(status_parser)
    status_parser.add_argument("--limit", type=int, default=20, help="Number of jobs to show.")
    status_parser.set_defaults(func=status_command)

    logs_parser = subparsers.add_parser("logs", help="Show job logs.")
    logs_parser.add_argument("job_id", help="Job ID.")
    add_config(logs_parser)
    logs_parser.add_argument("--limit", type=int, default=50, help="Number of log rows to show.")
    logs_parser.set_defaults(func=logs_command)

    return parser


def add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-c", "--config", default="config.toml", help="Path to config.toml.")


def init_command(args: argparse.Namespace) -> None:
    loaded = load_config(args.config)
    loaded.ensure_dirs()
    store = Store(loaded.db_path)
    try:
        store.init()
    finally:
        store.close()
    print(f"initialized {loaded.db_path}")


def enqueue_command(args: argparse.Namespace) -> None:
    minute_token = args.minute_token
    if args.minutes_url and not minute_token:
        tokens = extract_minute_tokens(args.minutes_url)
        minute_token = sorted(tokens)[0] if tokens else ""
    if not any([minute_token, args.meeting_id, args.calendar_event_id, args.media_path]):
        raise SystemExit("provide --minutes-url, --minute-token, --meeting-id, or --media-path")

    _, store = open_store(args.config)
    try:
        seed = seed_from_manual(
            minute_token=minute_token,
            meeting_id=args.meeting_id,
            calendar_event_id=args.calendar_event_id,
            project_hint=args.project_hint,
            media_path=args.media_path,
        )
        job = store.enqueue_seed(seed)
        print(f"queued {job.id}")
    finally:
        store.close()


def hook_command(args: argparse.Namespace) -> None:
    loaded, store = open_store(args.config)
    try:
        if args.stdin:
            for line in sys.stdin:
                process_event_line(line, loaded, store)
            return

        client = LarkClient(loaded)
        command = client.event_command()
        print("starting: " + " ".join(command), file=sys.stderr)
        process = subprocess.Popen(
            command,
            env=client.env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
        )
        assert process.stdout is not None
        for line in process.stdout:
            process_event_line(line, loaded, store)
        raise SystemExit(process.wait())
    finally:
        store.close()


def worker_command(args: argparse.Namespace) -> None:
    loaded, store = open_store(args.config)
    pipeline = Pipeline(loaded, store)
    try:
        while True:
            count = pipeline.process_due_once(limit=args.limit)
            if count:
                print(f"processed {count} job(s)")
            if args.once:
                return
            time.sleep(args.interval)
    finally:
        store.close()


def status_command(args: argparse.Namespace) -> None:
    _, store = open_store(args.config)
    try:
        print(render_jobs(store.list_jobs(limit=args.limit)))
    finally:
        store.close()


def logs_command(args: argparse.Namespace) -> None:
    _, store = open_store(args.config)
    try:
        for row in reversed(store.logs(args.job_id, limit=args.limit)):
            print(f"{row['created_at']} {row['level'].upper()} {row['message']} {row['data_json']}")
    finally:
        store.close()


def open_store(config_path: str | Path) -> tuple[Config, Store]:
    loaded = load_config(config_path)
    loaded.ensure_dirs()
    store = Store(loaded.db_path)
    store.init()
    return loaded, store


def process_event_line(line: str, config: Config, store: Store) -> None:
    line = line.strip()
    if not line:
        return
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        print(f"skip non-json event line: {line[:160]}", file=sys.stderr)
        return

    seeds = seeds_from_event(event, config.projects)
    if not seeds:
        print("event ignored: no minute/meeting/calendar id", file=sys.stderr)
        return
    for seed in seeds:
        job = store.enqueue_seed(seed)
        print(f"queued {job.id}")

