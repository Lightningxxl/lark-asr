import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

from lark_asr.config import AsrConfig, CodexConfig, Config, LarkConfig, PathsConfig, PipelineConfig
from lark_asr.events import seed_from_manual
from lark_asr.pipeline import Pipeline
from lark_asr.store import Store


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )


def configure_git_user(repo: Path) -> None:
    git(["config", "user.name", "Lark ASR Test"], repo)
    git(["config", "user.email", "lark-asr@example.invalid"], repo)


def init_knowledgebase_repo(root: Path) -> Path:
    origin = root / "origin.git"
    git(["init", "--bare", str(origin)], root)

    knowledgebase_dir = root / "kb"
    git(["clone", str(origin), str(knowledgebase_dir)], root)
    configure_git_user(knowledgebase_dir)
    git(["checkout", "-b", "main"], knowledgebase_dir)
    (knowledgebase_dir / "README.md").write_text("knowledgebase\n", encoding="utf-8")
    git(["add", "README.md"], knowledgebase_dir)
    git(["commit", "-m", "docs: init knowledgebase"], knowledgebase_dir)
    git(["push", "-u", "origin", "main"], knowledgebase_dir)
    git(["symbolic-ref", "HEAD", "refs/heads/main"], origin)
    return knowledgebase_dir


class PipelineTest(unittest.TestCase):
    def test_feishu_transcript_path_completes_without_asr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_cli = root / "fake-lark-cli"
            fake_cli.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import pathlib
                    import sys

                    args = sys.argv[1:]
                    if args[:2] == ["vc", "+notes"]:
                        output = pathlib.Path(args[args.index("--output-dir") + 1])
                        output.mkdir(parents=True, exist_ok=True)
                        (output / "transcript.md").write_text(
                            "Speaker 1: 智慧门店会议转写内容，已经由飞书生成，因此不需要本地 ASR。\\n" * 3,
                            encoding="utf-8",
                        )
                        print('{"ok": true}')
                        raise SystemExit(0)
                    raise SystemExit(2)
                    """
                ),
                encoding="utf-8",
            )
            os.chmod(fake_cli, 0o755)

            config = Config(
                paths=PathsConfig(
                    state_dir=root / "data",
                    work_dir=root / "work",
                    knowledgebase_dir=root / "kb",
                ),
                lark=LarkConfig(cli=str(fake_cli)),
                pipeline=PipelineConfig(minimum_transcript_chars=20),
            )
            config.ensure_dirs()
            store = Store(config.db_path)
            try:
                store.init()
                job = store.enqueue_seed(seed_from_manual(minute_token="obcn_test"))
                count = Pipeline(config, store).process_due_once()
                self.assertEqual(count, 1)
                updated = store.get(job.id)
                self.assertIsNotNone(updated)
                assert updated is not None
                self.assertEqual(updated.status, "completed")
                self.assertTrue(updated.transcript_path.endswith("transcript.md"))
            finally:
                store.close()

    def test_codex_auto_write_uses_full_auto(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_cli = root / "fake-lark-cli"
            fake_cli.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import pathlib
                    import sys

                    args = sys.argv[1:]
                    if args[:2] == ["vc", "+notes"]:
                        output = pathlib.Path(args[args.index("--output-dir") + 1])
                        output.mkdir(parents=True, exist_ok=True)
                        (output / "transcript.md").write_text(
                            "Speaker 1: 智慧门店会议转写内容，已经由飞书生成。\\n" * 3,
                            encoding="utf-8",
                        )
                        print('{"ok": true}')
                        raise SystemExit(0)
                    raise SystemExit(2)
                    """
                ),
                encoding="utf-8",
            )
            os.chmod(fake_cli, 0o755)

            fake_codex = root / "fake-codex"
            fake_codex.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json
                    import pathlib
                    import sys

                    (pathlib.Path.cwd() / "codex-args.json").write_text(
                        json.dumps(sys.argv[1:], ensure_ascii=False),
                        encoding="utf-8",
                    )
                    (pathlib.Path.cwd() / "codex-output.md").write_text(
                        "Codex 写入知识库内容。\\n",
                        encoding="utf-8",
                    )
                    raise SystemExit(0)
                    """
                ),
                encoding="utf-8",
            )
            os.chmod(fake_codex, 0o755)

            knowledgebase_dir = init_knowledgebase_repo(root)
            config = Config(
                paths=PathsConfig(
                    state_dir=root / "data",
                    work_dir=root / "work",
                    knowledgebase_dir=knowledgebase_dir,
                ),
                lark=LarkConfig(cli=str(fake_cli)),
                pipeline=PipelineConfig(minimum_transcript_chars=20, auto_kb_write=True),
                codex=CodexConfig(enabled=True, cmd=str(fake_codex), reasoning_effort=""),
            )
            config.ensure_dirs()
            store = Store(config.db_path)
            try:
                store.init()
                job = store.enqueue_seed(seed_from_manual(minute_token="obcn_test"))
                count = Pipeline(config, store).process_due_once()
                self.assertEqual(count, 1)
                updated = store.get(job.id)
                self.assertIsNotNone(updated)
                assert updated is not None
                self.assertEqual(updated.status, "completed")

                command = (knowledgebase_dir / "codex-args.json").read_text(encoding="utf-8")
                self.assertIn("--dangerously-bypass-approvals-and-sandbox", command)
                self.assertNotIn("--full-auto", command)
                self.assertIn("--skip-git-repo-check", command)
                self.assertNotIn('"-a"', command)
                self.assertNotIn("--sandbox", command)
                self.assertEqual(git(["status", "--porcelain"], knowledgebase_dir).stdout.strip(), "")
                self.assertIn(
                    "docs(asr): import meeting transcript",
                    git(["log", "--oneline", "origin/main", "-1"], knowledgebase_dir).stdout,
                )
            finally:
                store.close()

    def test_codex_auto_write_rebases_and_pushes_when_remote_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_cli = root / "fake-lark-cli"
            fake_cli.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import pathlib
                    import sys

                    args = sys.argv[1:]
                    if args[:2] == ["vc", "+notes"]:
                        output = pathlib.Path(args[args.index("--output-dir") + 1])
                        output.mkdir(parents=True, exist_ok=True)
                        (output / "transcript.md").write_text(
                            "Speaker 1: 智慧门店会议转写内容，已经由飞书生成。\\n" * 3,
                            encoding="utf-8",
                        )
                        print('{"ok": true}')
                        raise SystemExit(0)
                    raise SystemExit(2)
                    """
                ),
                encoding="utf-8",
            )
            os.chmod(fake_cli, 0o755)

            knowledgebase_dir = init_knowledgebase_repo(root)
            remote_clone = root / "remote-clone"
            git(["clone", "-b", "main", str(root / "origin.git"), str(remote_clone)], root)
            configure_git_user(remote_clone)

            fake_codex = root / "fake-codex"
            fake_codex.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import os
                    import pathlib
                    import subprocess

                    remote = pathlib.Path(os.environ["REMOTE_CLONE"])
                    (remote / "remote.md").write_text("remote moved while Codex ran\\n", encoding="utf-8")
                    subprocess.run(["git", "add", "remote.md"], cwd=remote, check=True)
                    subprocess.run(["git", "commit", "-m", "docs: remote move"], cwd=remote, check=True)
                    subprocess.run(["git", "push", "origin", "main"], cwd=remote, check=True)

                    (pathlib.Path.cwd() / "local.md").write_text("local Codex change\\n", encoding="utf-8")
                    raise SystemExit(0)
                    """
                ),
                encoding="utf-8",
            )
            os.chmod(fake_codex, 0o755)

            config = Config(
                paths=PathsConfig(
                    state_dir=root / "data",
                    work_dir=root / "work",
                    knowledgebase_dir=knowledgebase_dir,
                ),
                lark=LarkConfig(cli=str(fake_cli), env={"REMOTE_CLONE": str(remote_clone)}),
                pipeline=PipelineConfig(minimum_transcript_chars=20, auto_kb_write=True),
                codex=CodexConfig(enabled=True, cmd=str(fake_codex), reasoning_effort=""),
            )
            config.ensure_dirs()
            store = Store(config.db_path)
            try:
                store.init()
                job = store.enqueue_seed(seed_from_manual(minute_token="obcn_remote_move"))
                count = Pipeline(config, store).process_due_once()
                self.assertEqual(count, 1)
                updated = store.get(job.id)
                self.assertIsNotNone(updated)
                assert updated is not None
                self.assertEqual(updated.status, "completed")
                self.assertEqual(git(["status", "--porcelain"], knowledgebase_dir).stdout.strip(), "")

                remote_log = git(["log", "--oneline", "origin/main", "-3"], knowledgebase_dir).stdout
                self.assertIn("docs(asr): import meeting transcript", remote_log)
                self.assertIn("docs: remote move", remote_log)
            finally:
                store.close()

    def test_partial_feishu_transcript_falls_back_to_local_asr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_cli = root / "fake-lark-cli"
            fake_cli.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import pathlib
                    import sys

                    args = sys.argv[1:]
                    if args[:2] == ["vc", "+notes"]:
                        output = pathlib.Path(args[args.index("--output-dir") + 1])
                        output.mkdir(parents=True, exist_ok=True)
                        (output / "transcript.txt").write_text(
                            "2026-05-14 20:03:03 CST|16分钟 14秒\\n\\n"
                            "Shiki 00:00:00.160\\n开场。\\n\\n"
                            "RX 00:04:59.990\\n飞书只给到了前五分钟。\\n",
                            encoding="utf-8",
                        )
                        print('{"ok": true}')
                        raise SystemExit(0)
                    if args[:2] == ["minutes", "+download"]:
                        output = pathlib.Path(args[args.index("--output") + 1])
                        output.parent.mkdir(parents=True, exist_ok=True)
                        output.write_bytes(b"fake audio")
                        print('{"ok": true}')
                        raise SystemExit(0)
                    raise SystemExit(2)
                    """
                ),
                encoding="utf-8",
            )
            os.chmod(fake_cli, 0o755)

            fake_asr = root / "fake-asr.sh"
            fake_asr.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    set -euo pipefail
                    out_dir=""
                    while [ "$#" -gt 0 ]; do
                      case "$1" in
                        --output-dir) out_dir="$2"; shift 2 ;;
                        *) shift ;;
                      esac
                    done
                    mkdir -p "$out_dir"
                    mkdir -p "$out_dir/funasr"
                    printf 'FunASR 中间稿更长但不是最终稿。%.0s\\n' {1..20} > "$out_dir/funasr/raw.md"
                    printf '本地 ASR 最终转写，覆盖完整会议内容，因此应该进入知识库处理。\\n' > "$out_dir/transcript.md"
                    echo "$out_dir/transcript.md"
                    """
                ),
                encoding="utf-8",
            )
            os.chmod(fake_asr, 0o755)

            config = Config(
                paths=PathsConfig(
                    state_dir=root / "data",
                    work_dir=root / "work",
                    knowledgebase_dir=root / "kb",
                ),
                lark=LarkConfig(cli=str(fake_cli)),
                pipeline=PipelineConfig(minimum_transcript_chars=20),
                asr=AsrConfig(
                    enabled=True,
                    command=f"{fake_asr} --input {{media_path}} --output-dir {{job_dir}}/local_asr",
                    output_glob="local_asr/**/*.md",
                ),
            )
            config.ensure_dirs()
            store = Store(config.db_path)
            try:
                store.init()
                job = store.enqueue_seed(seed_from_manual(minute_token="obcn_partial"))
                count = Pipeline(config, store).process_due_once()
                self.assertEqual(count, 1)
                updated = store.get(job.id)
                self.assertIsNotNone(updated)
                assert updated is not None
                self.assertEqual(updated.status, "completed")
                self.assertIn("local_asr", updated.transcript_path)
                self.assertTrue(updated.transcript_path.endswith("local_asr/transcript.md"))
                self.assertTrue(Path(updated.media_path).exists())
            finally:
                store.close()

    def test_force_local_asr_skips_feishu_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_cli = root / "fake-lark-cli"
            fake_cli.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import pathlib
                    import sys

                    args = sys.argv[1:]
                    if args[:2] == ["vc", "+notes"]:
                        raise SystemExit(42)
                    if args[:2] == ["minutes", "+download"]:
                        output = pathlib.Path(args[args.index("--output") + 1])
                        output.parent.mkdir(parents=True, exist_ok=True)
                        output.write_bytes(b"fake audio")
                        print('{"ok": true}')
                        raise SystemExit(0)
                    raise SystemExit(2)
                    """
                ),
                encoding="utf-8",
            )
            os.chmod(fake_cli, 0o755)

            fake_asr = root / "fake-asr.sh"
            fake_asr.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    set -euo pipefail
                    out_dir=""
                    while [ "$#" -gt 0 ]; do
                      case "$1" in
                        --output-dir) out_dir="$2"; shift 2 ;;
                        *) shift ;;
                      esac
                    done
                    mkdir -p "$out_dir"
                    printf '强制本地 ASR 转写，跳过飞书文本结果，直接使用本地音频。\\n' > "$out_dir/transcript.md"
                    """
                ),
                encoding="utf-8",
            )
            os.chmod(fake_asr, 0o755)

            config = Config(
                paths=PathsConfig(
                    state_dir=root / "data",
                    work_dir=root / "work",
                    knowledgebase_dir=root / "kb",
                ),
                lark=LarkConfig(cli=str(fake_cli)),
                pipeline=PipelineConfig(minimum_transcript_chars=20, force_local_asr=True),
                asr=AsrConfig(
                    enabled=True,
                    command=f"{fake_asr} --input {{media_path}} --output-dir {{job_dir}}/local_asr",
                    output_glob="local_asr/**/*.md",
                ),
            )
            config.ensure_dirs()
            store = Store(config.db_path)
            try:
                store.init()
                job = store.enqueue_seed(seed_from_manual(minute_token="obcn_force"))
                count = Pipeline(config, store).process_due_once()
                self.assertEqual(count, 1)
                updated = store.get(job.id)
                self.assertIsNotNone(updated)
                assert updated is not None
                self.assertEqual(updated.status, "completed")
                self.assertIn("local_asr", updated.transcript_path)
                self.assertFalse((root / "work" / job.id / "feishu_notes").exists())
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
