#!/usr/bin/env python3
import argparse
import json
import re
import time
from pathlib import Path
from typing import Iterable

from funasr import AutoModel


def stamp(ms: int | float) -> str:
    total_ms = int(round(float(ms)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace(",", "，").replace("?", "？").replace("!", "！")
    text = re.sub(r"\s*([，。！？；：])\s*", r"\1", text)
    text = re.sub(r"([，。！？；：])\1+", r"\1", text)
    return text


def chunks(text: str, max_chars: int) -> Iterable[str]:
    text = normalize_text(text)
    while len(text) > max_chars:
        cut = max(text.rfind(mark, 0, max_chars) for mark in "，。！？；： ")
        if cut < max_chars // 2:
            cut = max_chars
        yield text[:cut].strip()
        text = text[cut:].strip()
    if text:
        yield text


def batched(items: list[str], size: int) -> Iterable[list[str]]:
    for idx in range(0, len(items), size):
        yield items[idx : idx + size]


def restore_segments(model: AutoModel, segments: list[dict], max_chars: int, batch_size: int) -> list[dict]:
    chunk_texts: list[str] = []
    chunk_refs: list[int] = []
    restored_parts: list[list[str]] = [[] for _ in segments]

    for idx, seg in enumerate(segments):
        parts = list(chunks(seg.get("text", ""), max_chars))
        if not parts:
            restored_parts[idx].append(seg.get("text", ""))
            continue
        for part in parts:
            chunk_texts.append(part)
            chunk_refs.append(idx)

    done = 0
    for batch in batched(chunk_texts, batch_size):
        result = model.generate(input=batch, cache={})
        for source, item in zip(batch, result or []):
            target_idx = chunk_refs[done]
            restored_parts[target_idx].append(str(item.get("text", source)))
            done += 1
        print(f"punctuated {done}/{len(chunk_texts)} chunks", flush=True)

    for idx, seg in enumerate(segments):
        seg["text"] = normalize_text("".join(restored_parts[idx]))
    return segments


def write_txt(path: Path, segments: list[dict]) -> None:
    lines = [
        f"[{stamp(seg['start_ms'])} - {stamp(seg['end_ms'])}] {seg['speaker']}: {seg['text']}"
        for seg in segments
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_md(path: Path, segments: list[dict], metadata: dict) -> None:
    lines = [
        "# Whisper large-v3 Transcript With Speakers And Punctuation",
        "",
        f"- Source: `{metadata.get('source')}`",
        f"- Punctuation model: `{metadata.get('punctuation_model')}`",
        f"- Punctuation elapsed: `{metadata.get('punctuation_elapsed_seconds')}` seconds",
        "",
        "| Time | Speaker | Text |",
        "|---|---|---|",
    ]
    for seg in segments:
        text = seg["text"].replace("|", "\\|")
        lines.append(f"| {stamp(seg['start_ms'])} - {stamp(seg['end_ms'])} | {seg['speaker']} | {text} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--out-prefix", required=True)
    parser.add_argument("--model", default="ct-punc")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--max-chars", type=int, default=260)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()

    input_path = Path(args.input_json)
    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    data = json.loads(input_path.read_text(encoding="utf-8"))
    segments = [dict(seg) for seg in data["segments"]]

    started = time.time()
    model = AutoModel(
        model=args.model,
        device=args.device,
        disable_update=True,
        disable_pbar=True,
    )
    segments = restore_segments(model, segments, args.max_chars, args.batch_size)

    metadata = dict(data.get("metadata", {}))
    metadata.update(
        {
            "source": str(input_path),
            "punctuation_model": args.model,
            "punctuation_elapsed_seconds": round(time.time() - started, 3),
        }
    )
    output = {"metadata": metadata, "segments": segments}
    json_path = Path(f"{out_prefix}.json")
    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    write_txt(Path(f"{out_prefix}.txt"), segments)
    write_md(Path(f"{out_prefix}.md"), segments, metadata)
    print(json_path)


if __name__ == "__main__":
    main()
