import unittest

from lark_asr.config import ProjectConfig
from lark_asr.events import extract_minute_tokens, seeds_from_event, seeds_from_minutes_search


class EventParsingTest(unittest.TestCase):
    def test_extract_minute_token_from_url(self):
        tokens = extract_minute_tokens("https://example.feishu.cn/minutes/obcn00000000000000000000")
        self.assertEqual(tokens, {"obcn00000000000000000000"})

    def test_seed_from_message_event_with_project_hint(self):
        event = {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "message": {
                    "content": "Demo Project会议 https://example.feishu.cn/minutes/obcn00000000000000000000"
                }
            },
        }
        seeds = seeds_from_event(
            event,
            [ProjectConfig(id="demo-project", path="projects/active/x", aliases=("Demo Project",))],
        )
        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0].minute_token, "obcn00000000000000000000")
        self.assertEqual(seeds[0].project_hint, "demo-project")
        self.assertEqual(seeds[0].event_type, "im.message.receive_v1")

    def test_seed_from_vc_event_with_meeting_id(self):
        event = {
            "header": {"event_type": "vc.meeting.ended_v1"},
            "event": {"meeting_id": "m_123"},
        }
        seeds = seeds_from_event(event)
        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0].meeting_id, "m_123")

    def test_seed_from_minutes_search_result(self):
        data = {
            "ok": True,
            "data": {
                "items": [
                    {
                        "display_info": "Demo Project周会",
                        "token": "obcn11111111111111111111",
                        "meta_data": {
                            "app_link": "https://example.feishu.cn/minutes/obcn11111111111111111111"
                        },
                    }
                ]
            },
        }
        seeds = seeds_from_minutes_search(
            data,
            [ProjectConfig(id="demo-project", path="projects/active/x", aliases=("Demo Project",))],
        )
        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0].source, "minutes_search")
        self.assertEqual(seeds[0].minute_token, "obcn11111111111111111111")
        self.assertEqual(seeds[0].project_hint, "demo-project")
        self.assertEqual(seeds[0].event_type, "minutes.search")


if __name__ == "__main__":
    unittest.main()
