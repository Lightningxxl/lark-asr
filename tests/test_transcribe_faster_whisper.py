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
    def test_loads_only_confirmed_hotwords_and_deduplicates(self):
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
            hotwords = module.load_confirmed_hotwords(path)

        self.assertEqual(hotwords, ["ORIA", "Claworld"])
        self.assertNotIn("ArkClaw", hotwords)

    def test_requires_confirmed_hotwords_section(self):
        module = load_script()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hotwords.md"
            path.write_text("# ASR热词库\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "已确认热词"):
                module.load_confirmed_hotwords(path)

    def test_builds_bounded_initial_prompt(self):
        module = load_script()
        prompt = module.build_initial_prompt(["ORIA", "Claworld", "WorkBuddy"])

        self.assertIn("ORIA", prompt)
        self.assertIn("Claworld", prompt)
        self.assertLessEqual(len(prompt), module.MAX_INITIAL_PROMPT_CHARS)


if __name__ == "__main__":
    unittest.main()
