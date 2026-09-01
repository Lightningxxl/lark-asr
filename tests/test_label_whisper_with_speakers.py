import importlib.util
from pathlib import Path
import unittest


def load_script():
    script = Path(__file__).resolve().parents[1] / "scripts" / "label_whisper_with_speakers.py"
    spec = importlib.util.spec_from_file_location("label_whisper_with_speakers", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class LabelWhisperWithSpeakersTest(unittest.TestCase):
    def test_segment_assignment_does_not_split_words_on_speaker_boundaries(self):
        module = load_script()
        whisper = {
            "segments": [
                {"start": 21.28, "end": 24.34, "text": "但接口不光是数据"},
                {"start": 24.34, "end": 25.90, "text": "就接口包括很多"},
                {"start": 25.90, "end": 26.66, "text": "还有些能力"},
            ]
        }
        diarization = {
            "segments": [
                {"start_ms": 21000, "end_ms": 23000, "speaker": "SPEAKER_00"},
                {"start_ms": 23000, "end_ms": 27000, "speaker": "SPEAKER_01"},
            ]
        }

        segments = module.label_segments(
            whisper,
            diarization,
            max_gap_ms=1200,
            max_nearest_ms=2500,
            max_segment_ms=30_000,
            max_segment_chars=180,
        )
        texts = [seg["text"] for seg in segments]

        self.assertIn("但接口不光是数据", texts)
        self.assertNotIn("但接", texts)
        self.assertNotIn("口不光是数据", texts)

    def test_adjacent_segments_are_merged_by_speaker_turn(self):
        module = load_script()
        whisper = {
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "第一句"},
                {"start": 1.2, "end": 2.0, "text": "第二句"},
                {"start": 3.8, "end": 4.2, "text": "第三句"},
            ]
        }
        diarization = {
            "segments": [
                {"start_ms": 0, "end_ms": 5000, "speaker": "SPEAKER_00"},
            ]
        }

        segments = module.label_segments(
            whisper,
            diarization,
            max_gap_ms=1200,
            max_nearest_ms=2500,
            max_segment_ms=30_000,
            max_segment_chars=180,
        )

        self.assertEqual([seg["text"] for seg in segments], ["第一句第二句", "第三句"])
        self.assertEqual([seg["speaker"] for seg in segments], ["SPEAKER_00", "SPEAKER_00"])

    def test_same_speaker_turn_is_capped_by_duration(self):
        module = load_script()
        whisper = {
            "segments": [
                {"start": 0.0, "end": 20.0, "text": "第一段"},
                {"start": 20.2, "end": 35.0, "text": "第二段"},
            ]
        }
        diarization = {
            "segments": [
                {"start_ms": 0, "end_ms": 40_000, "speaker": "SPEAKER_00"},
            ]
        }

        segments = module.label_segments(
            whisper,
            diarization,
            max_gap_ms=1200,
            max_nearest_ms=2500,
            max_segment_ms=30_000,
            max_segment_chars=180,
        )

        self.assertEqual([seg["text"] for seg in segments], ["第一段", "第二段"])

    def test_same_speaker_turn_is_capped_by_text_length(self):
        module = load_script()
        whisper = {
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "第一段文字"},
                {"start": 1.1, "end": 2.0, "text": "第二段文字"},
            ]
        }
        diarization = {
            "segments": [
                {"start_ms": 0, "end_ms": 3000, "speaker": "SPEAKER_00"},
            ]
        }

        segments = module.label_segments(
            whisper,
            diarization,
            max_gap_ms=1200,
            max_nearest_ms=2500,
            max_segment_ms=30_000,
            max_segment_chars=6,
        )

        self.assertEqual([seg["text"] for seg in segments], ["第一段文字", "第二段文字"])


if __name__ == "__main__":
    unittest.main()
