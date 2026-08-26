#!/usr/bin/env python3
import argparse
import json
import re
import time
from pathlib import Path


CONFIRMED_HOTWORDS_HEADING = "## 已确认热词"
MAX_TERMS = 100
MAX_TERMINOLOGY_CHARS = 2_400


def load_confirmed_terminology(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    in_confirmed_section = False
    found_confirmed_section = False
    terms: list[dict] = []
    seen_standards: set[str] = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            in_confirmed_section = line == CONFIRMED_HOTWORDS_HEADING
            found_confirmed_section = found_confirmed_section or in_confirmed_section
            continue
        if not in_confirmed_section or not line.startswith("|"):
            continue

        cells = [cell.strip().strip("`") for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        standard = cells[0]
        if standard == "标准写法" or not standard or set(standard) <= {"-", ":"}:
            continue

        key = standard.casefold()
        if key in seen_standards:
            continue
        aliases = []
        seen_aliases = {key}
        for alias in re.split(r"[、,，;；]", cells[1]):
            alias = alias.strip().strip("`")
            alias_key = alias.casefold()
            if alias and alias_key not in seen_aliases:
                aliases.append(alias)
                seen_aliases.add(alias_key)
        terms.append({"standard": standard, "aliases": aliases})
        seen_standards.add(key)

    if not found_confirmed_section:
        raise ValueError(f"missing {CONFIRMED_HOTWORDS_HEADING!r} in {path}")
    if not terms:
        raise ValueError(f"no confirmed terminology found in {path}")
    if len(terms) > MAX_TERMS:
        raise ValueError(f"confirmed terminology exceeds limit {MAX_TERMS}: {len(terms)}")
    terminology_chars = sum(
        len(term["standard"]) + sum(len(alias) for alias in term["aliases"])
        for term in terms
    )
    if terminology_chars > MAX_TERMINOLOGY_CHARS:
        raise ValueError(
            f"confirmed terminology exceeds character limit {MAX_TERMINOLOGY_CHARS}"
        )
    variant_owners: dict[str, str] = {}
    for term in terms:
        standard = term["standard"]
        for variant in [standard, *term["aliases"]]:
            key = variant.casefold()
            owner = variant_owners.get(key)
            if owner and owner.casefold() != standard.casefold():
                raise ValueError(
                    f"terminology variant {variant!r} maps to both {owner!r} and {standard!r}"
                )
            variant_owners[key] = standard
    return terms


def alias_pattern(alias: str) -> str:
    pattern = re.escape(alias)
    if alias[0].isascii() and alias[0].isalnum():
        pattern = rf"(?<![A-Za-z0-9]){pattern}"
    if alias[-1].isascii() and alias[-1].isalnum():
        pattern = rf"{pattern}(?![A-Za-z0-9])"
    return pattern


def normalize_terminology(text: str, terms: list[dict]) -> tuple[str, dict[str, int]]:
    normalized = text
    corrections: dict[str, int] = {}
    for term in terms:
        standard = term["standard"]
        variants = sorted([standard, *term["aliases"]], key=len, reverse=True)
        for variant in variants:
            pattern = alias_pattern(variant)

            def replace(match: re.Match) -> str:
                if match.group(0) != standard:
                    corrections[standard] = corrections.get(standard, 0) + 1
                return standard

            normalized = re.sub(pattern, replace, normalized, flags=re.IGNORECASE)
    return normalized, corrections


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
    parser.add_argument("--terminology-file", default=None)
    args = parser.parse_args()

    audio = Path(args.audio).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = audio.stem
    terminology_path = (
        Path(args.terminology_file).expanduser().resolve() if args.terminology_file else None
    )
    terminology = load_confirmed_terminology(terminology_path) if terminology_path else []

    started = time.time()
    from faster_whisper import WhisperModel

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
    terminology_corrections: dict[str, int] = {}
    for segment in segments_iter:
        normalized_text, corrections = normalize_terminology(segment.text, terminology)
        for standard, count in corrections.items():
            terminology_corrections[standard] = terminology_corrections.get(standard, 0) + count
        item = {
            "id": segment.id,
            "start": segment.start,
            "end": segment.end,
            "text": normalized_text,
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
        "terminology_source": str(terminology_path) if terminology_path else None,
        "terminology_count": len(terminology),
        "terminology_corrections": terminology_corrections,
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
