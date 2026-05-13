import unittest

from lark_asr.config import ProjectConfig
from lark_asr.events import extract_minute_tokens, seeds_from_event


class EventParsingTest(unittest.TestCase):
    def test_extract_minute_token_from_url(self):
        tokens = extract_minute_tokens("https://example.feishu.cn/minutes/obcnlhmgj4929j262r5gy1q5")
        self.assertEqual(tokens, {"obcnlhmgj4929j262r5gy1q5"})

    def test_seed_from_message_event_with_project_hint(self):
        event = {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "message": {
                    "content": "智慧门店会议 https://gcnb8zkig121.feishu.cn/minutes/obcnlhmgj4929j262r5gy1q5"
                }
            },
        }
        seeds = seeds_from_event(
            event,
            [ProjectConfig(id="smart-store", path="projects/active/x", aliases=("智慧门店",))],
        )
        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0].minute_token, "obcnlhmgj4929j262r5gy1q5")
        self.assertEqual(seeds[0].project_hint, "smart-store")
        self.assertEqual(seeds[0].event_type, "im.message.receive_v1")

    def test_seed_from_vc_event_with_meeting_id(self):
        event = {
            "header": {"event_type": "vc.meeting.ended_v1"},
            "event": {"meeting_id": "m_123"},
        }
        seeds = seeds_from_event(event)
        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0].meeting_id, "m_123")


if __name__ == "__main__":
    unittest.main()

