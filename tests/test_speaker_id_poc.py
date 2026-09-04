import importlib.util
import unittest
from pathlib import Path

import numpy as np


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "speaker_id_poc.py"
SPEC = importlib.util.spec_from_file_location("speaker_id_poc", SCRIPT_PATH)
speaker_id = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(speaker_id)


class SpeakerIdPocTests(unittest.TestCase):
    def test_merge_speaker_segments_respects_turns_and_chunk_limit(self):
        segments = [
            {"start_ms": 0, "end_ms": 3_000, "speaker": "SPEAKER_00"},
            {"start_ms": 3_400, "end_ms": 7_000, "speaker": "SPEAKER_00"},
            {"start_ms": 7_100, "end_ms": 8_000, "speaker": "SPEAKER_01"},
            {"start_ms": 8_200, "end_ms": 10_000, "speaker": "SPEAKER_00"},
            {"start_ms": 10_100, "end_ms": 25_000, "speaker": "SPEAKER_00"},
        ]

        grouped = speaker_id.merge_speaker_segments(
            segments, max_gap_ms=500, max_chunk_ms=15_000
        )

        self.assertEqual(
            [(item["start_ms"], item["end_ms"]) for item in grouped["SPEAKER_00"]],
            [(0, 7_000), (8_200, 10_000), (10_100, 25_000)],
        )
        self.assertEqual(len(grouped["SPEAKER_01"]), 1)

    def test_robust_centroid_discards_outlier(self):
        embeddings = [
            np.array([1.0, 0.00]),
            np.array([1.0, 0.02]),
            np.array([1.0, -0.02]),
            np.array([0.0, 1.00]),
        ]

        centroid, kept = speaker_id.robust_centroid(embeddings)

        self.assertEqual(len(kept), 3)
        self.assertGreater(centroid[0], 0.99)
        self.assertLess(abs(centroid[1]), 0.02)

    def test_global_assignment_applies_threshold_and_margin(self):
        labels = ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"]
        names = ["Xavier", "shiki"]
        scores = np.array(
            [
                [0.42, 0.83],
                [0.88, 0.51],
                [0.82, 0.43],
            ]
        )

        result = speaker_id.globally_assign(
            labels, names, scores, threshold=0.65, margin=0.08
        )

        self.assertEqual(result["SPEAKER_00"]["identity"], "shiki")
        self.assertEqual(result["SPEAKER_01"]["identity"], "Xavier")
        self.assertEqual(result["SPEAKER_02"]["identity"], "Xavier")

    def test_global_assignment_rejects_ambiguous_extra_cluster(self):
        result = speaker_id.globally_assign(
            ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"],
            ["Xavier", "shiki"],
            np.array([[0.42, 0.83], [0.88, 0.51], [0.57, 0.55]]),
            threshold=0.65,
            margin=0.08,
        )

        self.assertEqual(result["SPEAKER_02"]["identity"], "UNKNOWN")

    def test_similarity_matrix_uses_best_domain_prototype(self):
        identities = {
            "Xavier": [np.array([1.0, 0.0]), np.array([0.7, 0.7])],
            "shiki": [np.array([0.0, 1.0])],
        }
        speakers = {"SPEAKER_00": np.array([0.72, 0.69])}

        labels, names, scores = speaker_id.similarity_matrix(identities, speakers)

        self.assertEqual(labels, ["SPEAKER_00"])
        self.assertEqual(names, ["Xavier", "shiki"])
        self.assertGreater(scores[0, 0], scores[0, 1])


if __name__ == "__main__":
    unittest.main()
