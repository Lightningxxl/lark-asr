#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path


def stamp(seconds: float, sep: str = ".") -> str:
    millis = int(round(seconds * 1000))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{ms:03d}"


def norm_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"([\u4e00-\u9fff])\s+([\u4e00-\u9fff])", r"\1\2", text)
    return text


def best_speaker(start_ms: int, end_ms: int, speaker_segments: list[dict], max_nearest_ms: int) -> str:
    best_name = "UNKNOWN"
    best_overlap = 0
    midpoint = (start_ms + end_ms) // 2
    midpoint_name = None
    nearest_name = None
    nearest_distance = None

    for seg in speaker_segments:
        seg_start = int(seg["start_ms"])
        seg_end = int(seg["end_ms"])
        seg_name = seg.get("speaker") or "UNKNOWN"
        if seg_start <= midpoint <= seg_end:
            midpoint_name = seg_name
        overlap = max(0, min(end_ms, seg_end) - max(start_ms, seg_start))
        if overlap > best_overlap:
            best_overlap = overlap
            best_name = seg_name
        distance = min(abs(start_ms - seg_end), abs(end_ms - seg_start))
        if nearest_distance is None or distance < nearest_distance:
            nearest_distance = distance
            nearest_name = seg_name

    if midpoint_name:
        return midpoint_name
    if best_overlap > 0:
        return best_name
    if nearest_distance is not None and nearest_distance <= max_nearest_ms:
        return nearest_name or "UNKNOWN"
    return "UNKNOWN"


def word_items(whisper_segment: dict) -> list[dict]:
    words = whisper_segment.get("words") or []
    items = []
    for word in words:
        start = word.get("start")
        end = word.get("end")
        text = word.get("word") or ""
        if start is None or end is None or not text.strip():
            continue
        items.append({"start_ms": int(round(start * 1000)), "end_ms": int(round(end * 1000)), "text": text})

    if items:
        return items

    text = whisper_segment.get("text") or ""
    return [{
        "start_ms": int(round(float(whisper_segment["start"]) * 1000)),
        "end_ms": int(round(float(whisper_segment["end"]) * 1000)),
        "text": text,
    }]


def merge_segments(segments: list[dict], max_gap_ms: int) -> list[dict]:
    merged: list[dict] = []
    for seg in segments:
        if (
            merged
            and merged[-1]["speaker"] == seg["speaker"]
            and seg["start_ms"] - merged[-1]["end_ms"] <= max_gap_ms
        ):
            merged[-1]["end_ms"] = max(merged[-1]["end_ms"], seg["end_ms"])
            merged[-1]["text"] = norm_text(merged[-1]["text"] + seg["text"])
        else:
            merged.append(dict(seg))

    for idx, seg in enumerate(merged):
        seg["id"] = idx
    return merged


def label_words(whisper: dict, diarization: dict, max_gap_ms: int, max_nearest_ms: int) -> list[dict]:
    speaker_segments = sorted(diarization["segments"], key=lambda item: item["start_ms"])
    raw_groups: list[dict] = []

    for segment in whisper["segments"]:
        for item in word_items(segment):
            speaker = best_speaker(item["start_ms"], item["end_ms"], speaker_segments, max_nearest_ms)
            if (
                raw_groups
                and raw_groups[-1]["speaker"] == speaker
                and item["start_ms"] - raw_groups[-1]["end_ms"] <= max_gap_ms
            ):
                raw_groups[-1]["end_ms"] = max(raw_groups[-1]["end_ms"], item["end_ms"])
                raw_groups[-1]["text"] += item["text"]
            else:
                raw_groups.append({
                    "id": len(raw_groups),
                    "start_ms": item["start_ms"],
                    "end_ms": item["end_ms"],
                    "speaker": speaker,
                    "text": item["text"],
                })

    for group in raw_groups:
        group["text"] = norm_text(group["text"])
    return merge_segments([g for g in raw_groups if g["text"]], max_gap_ms)


def write_txt(path: Path, segments: list[dict]) -> None:
    lines = [
        f"[{stamp(seg['start_ms'] / 1000)} - {stamp(seg['end_ms'] / 1000)}] {seg['speaker']}: {seg['text']}"
        for seg in segments
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_md(path: Path, segments: list[dict], metadata: dict) -> None:
    lines = [
        "# Whisper large-v3 Transcript With Speakers",
        "",
        f"- ASR source: `{metadata.get('asr_source')}`",
        f"- Speaker source: `{metadata.get('speaker_source')}`",
        f"- ASR elapsed: `{metadata.get('asr_elapsed_seconds')}` seconds",
        "",
        "| Time | Speaker | Text |",
        "|---|---|---|",
    ]
    for seg in segments:
        text = seg["text"].replace("|", "\\|")
        lines.append(f"| {stamp(seg['start_ms'] / 1000)} - {stamp(seg['end_ms'] / 1000)} | {seg['speaker']} | {text} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--whisper-json", required=True)
    parser.add_argument("--speaker-json", required=True)
    parser.add_argument("--out-prefix", required=True)
    parser.add_argument("--max-gap-ms", type=int, default=1200)
    parser.add_argument("--max-nearest-ms", type=int, default=2500)
    args = parser.parse_args()

    whisper_path = Path(args.whisper_json)
    speaker_path = Path(args.speaker_json)
    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    whisper = json.loads(whisper_path.read_text(encoding="utf-8"))
    diarization = json.loads(speaker_path.read_text(encoding="utf-8"))
    segments = label_words(whisper, diarization, args.max_gap_ms, args.max_nearest_ms)

    metadata = {
        "asr_source": str(whisper_path),
        "speaker_source": str(speaker_path),
        "asr_metadata": whisper.get("metadata", {}),
        "speaker_metadata": diarization.get("metadata", {}),
        "asr_elapsed_seconds": whisper.get("metadata", {}).get("elapsed_seconds"),
        "speaker_assignment": "Whisper word timestamps assigned to FunASR/CAM++ diarization by midpoint/overlap.",
    }
    payload = {"metadata": metadata, "segments": segments}

    json_path = Path(f"{out_prefix}.json")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_txt(Path(f"{out_prefix}.txt"), segments)
    write_md(Path(f"{out_prefix}.md"), segments, metadata)
    print(json_path)


if __name__ == "__main__":
    main()
