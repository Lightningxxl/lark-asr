#!/usr/bin/env python3
import argparse
import json
import time
from pathlib import Path

from faster_whisper import WhisperModel


def stamp(seconds: float, sep: str = ".") -> str:
    millis = int(round(seconds * 1000))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{ms:03d}"


def write_srt(path: Path, segments: list[dict]) -> None:
    lines = []
    for idx, segment in enumerate(segments, start=1):
        lines.append(str(idx))
        lines.append(f"{stamp(segment['start'], ',')} --> {stamp(segment['end'], ',')}")
        lines.append(segment["text"].strip())
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_txt(path: Path, segments: list[dict]) -> None:
    lines = []
    for segment in segments:
        lines.append(
            f"[{stamp(segment['start'])} - {stamp(segment['end'])}] "
            f"{segment['text'].strip()}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_md(path: Path, segments: list[dict], metadata: dict) -> None:
    lines = [
        "# Transcript",
        "",
        f"- Source: `{metadata['source']}`",
        f"- Model: `{metadata['model']}`",
        f"- Language: `{metadata.get('language') or 'auto'}`",
        f"- Duration: `{metadata.get('duration')}` seconds",
        "",
        "| Time | Text |",
        "|---|---|",
    ]
    for segment in segments:
        text = segment["text"].strip().replace("|", "\\|")
        lines.append(f"| {stamp(segment['start'])} - {stamp(segment['end'])} | {text} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--model-dir", default=None)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--language", default=None)
    parser.add_argument("--beam-size", type=int, default=5)
    args = parser.parse_args()

    audio = Path(args.audio).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = audio.stem

    started = time.time()
    model = WhisperModel(
        args.model,
        device=args.device,
        compute_type=args.compute_type,
        download_root=args.model_dir,
    )
    segments_iter, info = model.transcribe(
        str(audio),
        language=args.language,
        beam_size=args.beam_size,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        word_timestamps=True,
        condition_on_previous_text=False,
    )

    segments = []
    for segment in segments_iter:
        item = {
            "id": segment.id,
            "start": segment.start,
            "end": segment.end,
            "text": segment.text,
            "avg_logprob": segment.avg_logprob,
            "compression_ratio": segment.compression_ratio,
            "no_speech_prob": segment.no_speech_prob,
            "words": [
                {
                    "start": word.start,
                    "end": word.end,
                    "word": word.word,
                    "probability": word.probability,
                }
                for word in (segment.words or [])
            ],
        }
        segments.append(item)
        print(
            f"[{stamp(item['start'])} - {stamp(item['end'])}] {item['text'].strip()}",
            flush=True,
        )

    metadata = {
        "source": str(audio),
        "model": args.model,
        "device": args.device,
        "compute_type": args.compute_type,
        "language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "duration": getattr(info, "duration", None),
        "duration_after_vad": getattr(info, "duration_after_vad", None),
        "elapsed_seconds": round(time.time() - started, 3),
    }
    payload = {"metadata": metadata, "segments": segments}

    json_path = out_dir / f"{stem}.large-v3.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_txt(out_dir / f"{stem}.large-v3.txt", segments)
    write_srt(out_dir / f"{stem}.large-v3.srt", segments)
    write_md(out_dir / f"{stem}.large-v3.md", segments, metadata)
    print(f"Wrote {json_path}", flush=True)


if __name__ == "__main__":
    main()
