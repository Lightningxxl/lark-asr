import importlib.util
from pathlib import Path
import tempfile
import unittest


def load_script():
    script = Path(__file__).resolve().parents[1] / "scripts" / "transcribe_faster_whisper.py"
    spec = importlib.util.spec_from_file_location("transcribe_faster_whisper", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TranscribeFasterWhisperTest(unittest.TestCase):
    def test_loads_only_confirmed_terminology_and_deduplicates(self):
        module = load_script()
        content = """# ASR热词库

## 已确认热词

| 标准写法 | 常见误识别 | 适用范围 | 依据 |
|---|---|---|---|
| ORIA | Aurea | 全局 | 用户确认 |
| `Claworld` | cloud.org | 全局 | 项目资料 |
| oria |  | 全局 | 重复项 |

## 候选热词

| 候选写法 | ASR 原文 | 来源会议 | 待确认事项 |
|---|---|---|---|
| ArkClaw | Art Cloud | 某会议 | 标准写法待确认 |
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hotwords.md"
            path.write_text(content, encoding="utf-8")
            terms = module.load_confirmed_terminology(path)

        self.assertEqual(
            terms,
            [
                {"standard": "ORIA", "aliases": ["Aurea"]},
                {"standard": "Claworld", "aliases": ["cloud.org"]},
            ],
        )
        self.assertNotIn("ArkClaw", [term["standard"] for term in terms])

    def test_requires_confirmed_hotwords_section(self):
        module = load_script()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hotwords.md"
            path.write_text("# ASR热词库\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "已确认热词"):
                module.load_confirmed_terminology(path)

    def test_normalizes_confirmed_aliases_without_partial_word_matches(self):
        module = load_script()
        terms = [
            {"standard": "ORIA", "aliases": ["Aurea", "欧瑞亚"]},
            {"standard": "Claude Code", "aliases": ["Cloud Code"]},
        ]

        text, corrections = module.normalize_terminology(
            "Aurea 和欧瑞亚使用 cloud code，但 cloud codes 是另一段文本。", terms
        )

        self.assertEqual(
            text,
            "ORIA 和ORIA使用 Claude Code，但 cloud codes 是另一段文本。",
        )
        self.assertEqual(corrections, {"ORIA": 2, "Claude Code": 1})

    def test_rejects_aliases_that_map_to_multiple_terms(self):
        module = load_script()
        content = """# ASR热词库

## 已确认热词

| 标准写法 | 常见误识别 | 适用范围 | 依据 |
|---|---|---|---|
| Claworld | cloud.org | 全局 | 产品名 |
| claworld.love | cloud.org | 全局 | 域名 |
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hotwords.md"
            path.write_text(content, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "maps to both"):
                module.load_confirmed_terminology(path)

if __name__ == "__main__":
    unittest.main()
