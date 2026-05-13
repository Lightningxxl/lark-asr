import os
from pathlib import Path
import tempfile
import textwrap
import unittest

from lark_asr.config import Config, LarkConfig, PathsConfig, PipelineConfig
from lark_asr.events import seed_from_manual
from lark_asr.pipeline import Pipeline
from lark_asr.store import Store


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


if __name__ == "__main__":
    unittest.main()

