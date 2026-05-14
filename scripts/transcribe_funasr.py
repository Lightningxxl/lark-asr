#!/usr/bin/env python3
import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

from funasr import AutoModel


def stamp(ms: int | float) -> str:
    total_ms = int(round(float(ms)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def normalize_text(text: Any) -> str:
    if text is None:
        return ""
    if isinstance(text, list):
        return "".join(str(x) for x in text)
    return str(text)


def first_present(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return None


def normalize_speaker(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        return f"SPEAKER_{value:02d}"
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return f"SPEAKER_{int(text):02d}"
    return text


def extract_segments(result: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for item in result:
        text = normalize_text(item.get("text")).strip()
        sentence_info = item.get("sentence_info") or item.get("sentences") or []
        if sentence_info:
            for idx, sent in enumerate(sentence_info):
                sent_text = normalize_text(sent.get("text")).strip()
                start = sent.get("start") or sent.get("timestamp", [0, 0])[0]
                end = sent.get("end") or sent.get("timestamp", [0, 0])[-1]
                speaker = first_present(sent, "spk", "speaker", "spk_id")
                segments.append(
                    {
                        "id": len(segments),
                        "start_ms": int(start or 0),
                        "end_ms": int(end or 0),
                        "speaker": normalize_speaker(speaker),
                        "text": sent_text,
                        "raw": sent,
                    }
                )
        elif text:
            segments.append(
                {
                    "id": len(segments),
                    "start_ms": 0,
                    "end_ms": 0,
                    "speaker": normalize_speaker(first_present(item, "spk", "speaker", "spk_id")),
                    "text": text,
                    "raw": item,
                }
            )
    return segments


def write_txt(path: Path, segments: list[dict[str, Any]]) -> None:
    lines = []
    for seg in segments:
        speaker = seg.get("speaker") or "SPEAKER_??"
        lines.append(
            f"[{stamp(seg['start_ms'])} - {stamp(seg['end_ms'])}] {speaker}: {seg['text']}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_md(path: Path, segments: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    lines = [
        "# Transcript",
        "",
        f"- Source: `{metadata['source']}`",
        f"- ASR model: `{metadata['model']}`",
        f"- VAD model: `{metadata['vad_model']}`",
        f"- Punctuation model: `{metadata['punc_model']}`",
        f"- Speaker model: `{metadata['spk_model']}`",
        "",
        "| Time | Speaker | Text |",
        "|---|---|---|",
    ]
    for seg in segments:
        text = re.sub(r"\s+", " ", seg["text"]).replace("|", "\\|")
        speaker = seg.get("speaker") or "SPEAKER_??"
        lines.append(f"| {stamp(seg['start_ms'])} - {stamp(seg['end_ms'])} | {speaker} | {text} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--model",
        default="iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    )
    parser.add_argument("--vad-model", default="fsmn-vad")
    parser.add_argument("--punc-model", default="ct-punc")
    parser.add_argument("--spk-model", default="cam++")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--batch-size-s", type=int, default=300)
    args = parser.parse_args()

    audio = Path(args.audio).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()

    model = AutoModel(
        model=args.model,
        vad_model=args.vad_model,
        punc_model=args.punc_model,
        spk_model=args.spk_model,
        device=args.device,
        disable_update=True,
    )
    result = model.generate(
        input=str(audio),
        cache={},
        language="auto",
        use_itn=True,
        batch_size_s=args.batch_size_s,
        merge_vad=True,
        merge_length_s=15,
    )

    segments = extract_segments(result)
    metadata = {
        "source": str(audio),
        "model": args.model,
        "vad_model": args.vad_model,
        "punc_model": args.punc_model,
        "spk_model": args.spk_model,
        "device": args.device,
        "elapsed_seconds": round(time.time() - started, 3),
    }

    stem = audio.stem
    payload = {"metadata": metadata, "segments": segments, "raw_result": result}
    (out_dir / f"{stem}.funasr.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_txt(out_dir / f"{stem}.funasr.txt", segments)
    write_md(out_dir / f"{stem}.funasr.md", segments, metadata)
    for seg in segments:
        speaker = seg.get("speaker") or "SPEAKER_??"
        print(f"[{stamp(seg['start_ms'])} - {stamp(seg['end_ms'])}] {speaker}: {seg['text']}", flush=True)


if __name__ == "__main__":
    main()
