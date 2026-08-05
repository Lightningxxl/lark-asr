import os
from pathlib import Path
import tempfile
import textwrap
import unittest

from lark_asr.cli import main
from lark_asr.config import load_config
from lark_asr.store import Store


class PollCommandTest(unittest.TestCase):
    def test_poll_once_combines_minutes_and_recorded_meeting_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_cli = root / "fake-lark-cli"
            fake_cli.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json
                    import sys

                    args = sys.argv[1:]
                    if args[:2] == ["event", "+subscribe"]:
                        raise SystemExit(9)
                    if args[:2] == ["minutes", "+search"]:
                        print(json.dumps({
                            "data": {
                                "items": [
                                    {
                                        "token": "obcnowni21y3jlyo87x5us62",
                                        "display_info": "智慧门店周会",
                                    }
                                ]
                            }
                        }))
                        raise SystemExit(0)
                    if args[:2] == ["vc", "+search"]:
                        print(json.dumps({
                            "data": {
                                "items": [
                                    {"id": "meeting_duplicate", "display_info": "智慧门店周会"},
                                    {"id": "meeting_external", "display_info": "外部宠物行业会议"},
                                    {"id": "meeting_without_recording", "display_info": "未录制会议"},
                                ]
                            }
                        }))
                        raise SystemExit(0)
                    if args[:2] == ["vc", "+recording"]:
                        meeting_id = args[args.index("--meeting-ids") + 1]
                        tokens = {
                            "meeting_duplicate": "obcnowni21y3jlyo87x5us62",
                            "meeting_external": "obcnexternal1234567890",
                        }
                        recording = {"meeting_id": meeting_id}
                        if meeting_id in tokens:
                            recording["minute_token"] = tokens[meeting_id]
                        print(json.dumps({"data": {"recordings": [recording]}}))
                        raise SystemExit(0)
                    raise SystemExit(2)
                    """
                ),
                encoding="utf-8",
            )
            os.chmod(fake_cli, 0o755)

            config_path = root / "config.toml"
            config_path.write_text(
                textwrap.dedent(
                    f"""\
                    [paths]
                    state_dir = "{root / "data"}"
                    work_dir = "{root / "work"}"
                    knowledgebase_dir = "{root / "kb"}"

                    [lark]
                    cli = "{fake_cli}"
                    event_enabled = false
                    minutes_backfill_enabled = true
                    minutes_backfill_interval_seconds = 300
                    """
                ),
                encoding="utf-8",
            )

            main(["poll", "--config", str(config_path), "--once"])

            config = load_config(config_path)
            store = Store(config.db_path)
            try:
                jobs = store.list_jobs(limit=5)
            finally:
                store.close()
            self.assertEqual(len(jobs), 2)
            by_id = {job.id: job for job in jobs}
            self.assertEqual(
                by_id["minute:obcnowni21y3jlyo87x5us62"].source,
                "minutes_search",
            )
            external = by_id["minute:obcnexternal1234567890"]
            self.assertEqual(external.source, "vc_search")
            self.assertEqual(external.meeting_id, "meeting_external")


if __name__ == "__main__":
    unittest.main()
