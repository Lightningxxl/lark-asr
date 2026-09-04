#!/usr/bin/env python3
"""Enroll and apply conservative closed-set speaker identities to diarized meetings."""

from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np


def normalize(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if norm == 0:
        raise ValueError("speaker embedding has zero norm")
    return vector / norm


def merge_speaker_segments(
    segments: list[dict[str, Any]],
    *,
    max_gap_ms: int = 800,
    max_chunk_ms: int = 20_000,
) -> dict[str, list[dict[str, Any]]]:
    """Build single-speaker chunks from consecutive diarization segments."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    current: dict[str, Any] | None = None

    for segment in sorted(segments, key=lambda item: (item["start_ms"], item["end_ms"])):
        speaker = str(segment.get("speaker") or "UNKNOWN")
        start_ms = int(segment["start_ms"])
        end_ms = int(segment["end_ms"])
        if end_ms <= start_ms:
            continue

        can_merge = (
            current is not None
            and current["speaker"] == speaker
            and start_ms - current["end_ms"] <= max_gap_ms
            and end_ms - current["start_ms"] <= max_chunk_ms
        )
        if can_merge:
            current["end_ms"] = max(current["end_ms"], end_ms)
            current["segment_count"] += 1
            continue

        if current is not None:
            grouped[current["speaker"]].append(current)
        current = {
            "speaker": speaker,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "segment_count": 1,
        }

    if current is not None:
        grouped[current["speaker"]].append(current)
    return dict(grouped)


def select_chunks(
    chunks: list[dict[str, Any]],
    *,
    minimum_ms: int = 4_000,
    limit: int = 12,
) -> list[dict[str, Any]]:
    eligible = [
        chunk
        for chunk in chunks
        if int(chunk["end_ms"]) - int(chunk["start_ms"]) >= minimum_ms
    ]
    return sorted(
        eligible,
        key=lambda chunk: (
            -(int(chunk["end_ms"]) - int(chunk["start_ms"])),
            int(chunk["start_ms"]),
        ),
    )[:limit]


def sample_timeline_chunks(
    chunks: list[dict[str, Any]],
    *,
    minimum_ms: int = 4_000,
    limit: int = 120,
) -> list[dict[str, Any]]:
    """Keep useful chunks distributed over the full meeting timeline."""
    eligible = [
        chunk
        for chunk in chunks
        if int(chunk["end_ms"]) - int(chunk["start_ms"]) >= minimum_ms
    ]
    if len(eligible) <= limit:
        return eligible
    indexes = np.linspace(0, len(eligible) - 1, num=limit, dtype=int)
    return [eligible[int(index)] for index in indexes]


def robust_centroid(embeddings: list[np.ndarray]) -> tuple[np.ndarray, list[int]]:
    if not embeddings:
        raise ValueError("at least one embedding is required")
    matrix = np.stack([normalize(item) for item in embeddings])
    if len(matrix) <= 2:
        kept = list(range(len(matrix)))
    else:
        similarities = matrix @ matrix.T
        mean_similarity = (similarities.sum(axis=1) - 1.0) / (len(matrix) - 1)
        keep_count = max(2, math.ceil(len(matrix) * 0.75))
        kept = np.argsort(mean_similarity)[-keep_count:].tolist()
    return normalize(matrix[kept].mean(axis=0)), kept


def similarity_matrix(
    identities: dict[str, list[np.ndarray]],
    speakers: dict[str, np.ndarray],
) -> tuple[list[str], list[str], np.ndarray]:
    names = sorted(identities)
    labels = sorted(speakers)
    matrix = np.asarray(
        [
            [
                max(
                    float(normalize(speakers[label]) @ normalize(prototype))
                    for prototype in identities[name]
                )
                for name in names
            ]
            for label in labels
        ],
        dtype=np.float32,
    )
    return labels, names, matrix


def globally_assign(
    labels: list[str],
    names: list[str],
    scores: np.ndarray,
    *,
    threshold: float,
    margin: float,
) -> dict[str, dict[str, Any]]:
    """Map clusters to identities, retaining UNKNOWN below confidence gates."""
    if not labels or not names:
        return {}

    selected: dict[int, int]
    if len(labels) == len(names):
        best_total = -math.inf
        best_pairs: list[tuple[int, int]] = []
        for name_indexes in itertools.permutations(range(len(names)), len(labels)):
            pairs = list(zip(range(len(labels)), name_indexes))
            total = sum(float(scores[label_index, name_index]) for label_index, name_index in pairs)
            if total > best_total:
                best_total = total
                best_pairs = pairs
        selected = {label_index: name_index for label_index, name_index in best_pairs}
    else:
        # Diarization can split one real person into multiple anonymous clusters.
        selected = {
            label_index: int(np.argmax(scores[label_index]))
            for label_index in range(len(labels))
        }
    output: dict[str, dict[str, Any]] = {}
    for label_index, label in enumerate(labels):
        ranked = np.argsort(scores[label_index])[::-1]
        best_index = int(ranked[0])
        runner_up = float(scores[label_index, ranked[1]]) if len(ranked) > 1 else -1.0
        assigned_index = selected.get(label_index)
        assigned_score = float(scores[label_index, assigned_index]) if assigned_index is not None else -1.0
        assigned_margin = assigned_score - runner_up
        accepted = (
            assigned_index is not None
            and assigned_index == best_index
            and assigned_score >= threshold
            and assigned_margin >= margin
        )
        output[label] = {
            "identity": names[assigned_index] if accepted else "UNKNOWN",
            "score": round(assigned_score, 6),
            "runner_up": names[int(ranked[1])] if len(ranked) > 1 else None,
            "runner_up_score": round(runner_up, 6),
            "margin": round(assigned_margin, 6),
            "accepted": accepted,
        }
    return output


def load_segments(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    segments = payload.get("segments")
    if not isinstance(segments, list):
        raise ValueError(f"{path} does not contain a segments list")
    return segments


def parse_labels(values: list[str]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for value in values:
        speaker, separator, identity = value.partition("=")
        if not separator or not speaker.strip() or not identity.strip():
            raise ValueError(f"invalid label mapping: {value!r}")
        labels[speaker.strip()] = identity.strip()
    return labels


def profile_prototypes(value: Any) -> list[np.ndarray]:
    array = np.asarray(value, dtype=np.float32)
    if array.ndim == 1:
        return [normalize(array)]
    if array.ndim == 2:
        return [normalize(row) for row in array]
    raise ValueError("identity profile must be one vector or a list of vectors")


def load_audio_chunk(
    path: Path,
    start_ms: int,
    end_ms: int,
    sample_rate: int = 16_000,
) -> np.ndarray:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start_ms / 1000:.3f}",
        "-i",
        str(path),
        "-t",
        f"{(end_ms - start_ms) / 1000:.3f}",
        "-f",
        "f32le",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "pipe:1",
    ]
    completed = subprocess.run(command, check=True, stdout=subprocess.PIPE)
    return np.frombuffer(completed.stdout, dtype="<f4").copy()


def extract_embeddings(
    audio_path: Path,
    chunks: list[dict[str, Any]],
    *,
    model: Any,
    batch_size: int = 16,
) -> list[np.ndarray]:
    embeddings: list[np.ndarray] = []
    for offset in range(0, len(chunks), batch_size):
        batch = chunks[offset : offset + batch_size]
        samples = [
            load_audio_chunk(
                audio_path,
                max(0, int(chunk["start_ms"])),
                int(chunk["end_ms"]),
            )
            for chunk in batch
        ]
        results = model.generate(input=samples, cache={}, is_final=True)
        for result in results:
            embedding = result["spk_embedding"]
            if hasattr(embedding, "detach"):
                embedding = embedding.detach().cpu().numpy()
            embeddings.append(normalize(np.asarray(embedding)))
    return embeddings


def prepare_speaker_embeddings(
    audio_path: Path,
    segments_path: Path,
    *,
    model_name: str,
    device: str,
    minimum_ms: int,
    chunk_limit: int,
    sample_timeline: bool,
) -> tuple[dict[str, list[np.ndarray]], dict[str, list[dict[str, Any]]]]:
    from funasr import AutoModel

    grouped = merge_speaker_segments(load_segments(segments_path))
    selector = sample_timeline_chunks if sample_timeline else select_chunks
    selected = {
        speaker: selector(chunks, minimum_ms=minimum_ms, limit=chunk_limit)
        for speaker, chunks in grouped.items()
    }
    selected = {speaker: chunks for speaker, chunks in selected.items() if chunks}
    model = AutoModel(model=model_name, device=device, disable_update=True)
    embeddings: dict[str, list[np.ndarray]] = {}
    for speaker, chunks in sorted(selected.items()):
        embeddings[speaker] = extract_embeddings(
            audio_path,
            chunks,
            model=model,
        )
    return embeddings, selected


def enroll(args: argparse.Namespace) -> None:
    mapping = parse_labels(args.label)
    embeddings, chunks = prepare_speaker_embeddings(
        args.audio,
        args.segments,
        model_name=args.model,
        device=args.device,
        minimum_ms=args.minimum_ms,
        chunk_limit=args.chunk_limit,
        sample_timeline=False,
    )

    identities: dict[str, list[np.ndarray]] = defaultdict(list)
    source: dict[str, Any] = {}
    holdout_scores: list[dict[str, Any]] = []
    for speaker, identity in mapping.items():
        available = embeddings.get(speaker, [])
        if len(available) < 2:
            raise ValueError(f"{speaker} has fewer than two eligible chunks")
        ordered = sorted(
            zip(chunks[speaker], available),
            key=lambda item: int(item[0]["start_ms"]),
        )
        enrollment = [embedding for index, (_, embedding) in enumerate(ordered) if index % 3 != 2]
        holdout = [embedding for index, (_, embedding) in enumerate(ordered) if index % 3 == 2]
        centroid, kept = robust_centroid(enrollment)
        identities[identity].append(centroid)
        source[identity] = {
            "speaker": speaker,
            "enrollment_chunks": len(enrollment),
            "kept_chunks": len(kept),
            "holdout_chunks": len(holdout),
            "selected_ranges_ms": [
                [int(chunk["start_ms"]), int(chunk["end_ms"])] for chunk, _ in ordered
            ],
        }
        for embedding in holdout:
            holdout_scores.append({"identity": identity, "embedding": embedding})

    centroids = {
        identity: robust_centroid(items)[0] for identity, items in identities.items()
    }
    positive_scores = []
    negative_scores = []
    holdout_results = []
    for item in holdout_scores:
        identity = item["identity"]
        scores = {
            name: float(item["embedding"] @ centroid) for name, centroid in centroids.items()
        }
        positive_scores.append(scores[identity])
        negative_scores.extend(score for name, score in scores.items() if name != identity)
        predicted = max(scores, key=scores.get)
        holdout_results.append(
            {
                "expected": identity,
                "predicted": predicted,
                "scores": {name: round(score, 6) for name, score in scores.items()},
            }
        )

    minimum_positive = min(positive_scores) if positive_scores else 0.0
    maximum_negative = max(negative_scores) if negative_scores else -1.0
    threshold = (minimum_positive + maximum_negative) / 2
    profile = {
        "schema_version": 1,
        "model": args.model,
        "sample_rate": 16_000,
        "identities": {name: centroid.tolist() for name, centroid in centroids.items()},
        "calibration": {
            "minimum_positive": round(minimum_positive, 6),
            "maximum_negative": round(maximum_negative, 6),
            "suggested_threshold": round(threshold, 6),
            "suggested_margin": round(max(0.02, (minimum_positive - maximum_negative) / 4), 6),
            "holdout_accuracy": round(
                sum(item["expected"] == item["predicted"] for item in holdout_results)
                / len(holdout_results),
                6,
            )
            if holdout_results
            else None,
            "holdout": holdout_results,
        },
        "source": {
            "audio": str(args.audio),
            "segments": str(args.segments),
            "speakers": source,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(profile, ensure_ascii=False, indent=2))


def score_embedding(
    embedding: np.ndarray,
    identities: dict[str, list[np.ndarray]],
    *,
    threshold: float,
    margin: float,
) -> dict[str, Any]:
    scores = {
        name: max(float(normalize(embedding) @ prototype) for prototype in prototypes)
        for name, prototypes in identities.items()
    }
    ranked = sorted(scores, key=scores.get, reverse=True)
    best_name = ranked[0]
    best_score = scores[best_name]
    runner_up = ranked[1] if len(ranked) > 1 else None
    runner_up_score = scores[runner_up] if runner_up is not None else -1.0
    accepted = best_score >= threshold and best_score - runner_up_score >= margin
    return {
        "identity": best_name if accepted else "UNKNOWN",
        "score": round(best_score, 6),
        "runner_up": runner_up,
        "runner_up_score": round(runner_up_score, 6),
        "margin": round(best_score - runner_up_score, 6),
        "accepted": accepted,
        "scores": {name: round(score, 6) for name, score in scores.items()},
    }


def decide_cluster_mode(
    cluster_assignment: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    mixed_min_chunks: int = 2,
    mixed_min_duration_ms: int = 8_000,
) -> dict[str, Any]:
    accepted_duration: dict[str, int] = defaultdict(int)
    accepted_chunks: dict[str, int] = defaultdict(int)
    for item in evidence:
        identity = item["identity"]
        if identity == "UNKNOWN":
            continue
        start_ms, end_ms = item["range_ms"]
        accepted_duration[identity] += max(0, int(end_ms) - int(start_ms))
        accepted_chunks[identity] += 1

    material_identities = sorted(
        identity
        for identity, duration_ms in accepted_duration.items()
        if accepted_chunks[identity] >= mixed_min_chunks
        and duration_ms >= mixed_min_duration_ms
    )
    cluster_identity = cluster_assignment.get("identity", "UNKNOWN")
    conflicting = [name for name in material_identities if name != cluster_identity]
    if cluster_identity != "UNKNOWN" and not conflicting:
        mode = "stable"
        identity = cluster_identity
    elif len(material_identities) >= 2:
        mode = "mixed"
        identity = None
    elif material_identities:
        mode = "partial"
        identity = None
    else:
        mode = "unidentified"
        identity = None
    return {
        "mode": mode,
        "identity": identity,
        "material_identities": material_identities,
        "accepted_duration_ms": dict(sorted(accepted_duration.items())),
        "accepted_chunks": dict(sorted(accepted_chunks.items())),
    }


def analyze_speakers(args: argparse.Namespace) -> dict[str, Any]:
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    model_name = args.model or profile["model"]
    identities = {
        name: profile_prototypes(vector)
        for name, vector in profile["identities"].items()
    }
    embeddings, chunks = prepare_speaker_embeddings(
        args.audio,
        args.segments,
        model_name=model_name,
        device=args.device,
        minimum_ms=args.minimum_ms,
        chunk_limit=args.timeline_limit,
        sample_timeline=True,
    )
    speaker_centroids: dict[str, np.ndarray] = {}
    for speaker, speaker_embeddings in embeddings.items():
        pairs = sorted(
            zip(chunks[speaker], speaker_embeddings),
            key=lambda item: -(
                int(item[0]["end_ms"]) - int(item[0]["start_ms"])
            ),
        )[: args.chunk_limit]
        speaker_centroids[speaker] = robust_centroid(
            [embedding for _, embedding in pairs]
        )[0]
    labels, names, scores = similarity_matrix(identities, speaker_centroids)
    calibration = profile.get("calibration", {})
    threshold = args.threshold
    if threshold is None:
        threshold = float(calibration.get("suggested_threshold", 0.65))
    margin = args.margin
    if margin is None:
        margin = float(calibration.get("suggested_margin", 0.04))
    assignments = globally_assign(labels, names, scores, threshold=threshold, margin=margin)

    chunk_evidence: dict[str, list[dict[str, Any]]] = {}
    for speaker, speaker_embeddings in embeddings.items():
        evidence = []
        for chunk, embedding in zip(chunks[speaker], speaker_embeddings):
            item = score_embedding(
                embedding,
                identities,
                threshold=threshold,
                margin=margin,
            )
            item["range_ms"] = [int(chunk["start_ms"]), int(chunk["end_ms"])]
            evidence.append(item)
        chunk_evidence[speaker] = evidence

    decisions = {
        speaker: decide_cluster_mode(assignments[speaker], chunk_evidence[speaker])
        for speaker in assignments
    }

    result = {
        "audio": str(args.audio),
        "segments": str(args.segments),
        "model": model_name,
        "threshold": threshold,
        "margin": margin,
        "score_matrix": {
            label: {name: round(float(scores[row, column]), 6) for column, name in enumerate(names)}
            for row, label in enumerate(labels)
        },
        "assignments": assignments,
        "decisions": decisions,
        "chunk_evidence": chunk_evidence,
        "selected_chunks": {
            speaker: [
                [int(chunk["start_ms"]), int(chunk["end_ms"])] for chunk in speaker_chunks
            ]
            for speaker, speaker_chunks in chunks.items()
        },
    }
    return result


def identify(args: argparse.Namespace) -> None:
    result = analyze_speakers(args)
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")


def nearest_evidence_identity(
    segment: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    max_nearest_ms: int,
) -> str | None:
    start_ms = int(segment["start_ms"])
    end_ms = int(segment["end_ms"])
    best: tuple[int, int, str] | None = None
    for item in evidence:
        identity = item["identity"]
        if identity == "UNKNOWN":
            continue
        item_start, item_end = (int(value) for value in item["range_ms"])
        overlap = max(0, min(end_ms, item_end) - max(start_ms, item_start))
        distance = 0 if overlap else min(abs(start_ms - item_end), abs(end_ms - item_start))
        candidate = (overlap, -distance, identity)
        if best is None or candidate > best:
            best = candidate
    if best is None or (best[0] == 0 and -best[1] > max_nearest_ms):
        return None
    return best[2]


def relabel_segments(
    segments: list[dict[str, Any]],
    report: dict[str, Any],
    *,
    max_nearest_ms: int = 30_000,
) -> list[dict[str, Any]]:
    output = []
    for original in segments:
        segment = dict(original)
        speaker = str(segment.get("speaker") or "UNKNOWN")
        decision = report["decisions"].get(speaker, {"mode": "unidentified"})
        identity: str | None = None
        if decision["mode"] == "stable":
            identity = decision["identity"]
        elif decision["mode"] in {"mixed", "partial"}:
            identity = nearest_evidence_identity(
                segment,
                report["chunk_evidence"].get(speaker, []),
                max_nearest_ms=max_nearest_ms,
            )
        if identity:
            segment["speaker_diarization"] = speaker
            segment["speaker"] = identity
        output.append(segment)
    return output


def stamp(ms: int | float) -> str:
    total_ms = int(round(float(ms)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def write_transcript(prefix: Path, payload: dict[str, Any]) -> None:
    segments = payload["segments"]
    txt_lines = [
        f"[{stamp(item['start_ms'])} - {stamp(item['end_ms'])}] "
        f"{item.get('speaker') or 'SPEAKER_??'}: {item.get('text', '')}"
        for item in segments
    ]
    Path(f"{prefix}.txt").write_text("\n".join(txt_lines) + "\n", encoding="utf-8")

    metadata = payload.get("metadata", {})
    md_lines = [
        "# Transcript",
        "",
        f"- Source: `{metadata.get('source', '')}`",
        f"- ASR model: `{metadata.get('model', '')}`",
        f"- Speaker model: `{metadata.get('spk_model', '')}`",
        f"- Speaker profile: `{metadata.get('speaker_identity', {}).get('profile', '')}`",
        "",
        "| Time | Speaker | Text |",
        "|---|---|---|",
    ]
    for item in segments:
        text = re.sub(r"\s+", " ", str(item.get("text", ""))).replace("|", "\\|")
        speaker = item.get("speaker") or "SPEAKER_??"
        md_lines.append(
            f"| {stamp(item['start_ms'])} - {stamp(item['end_ms'])} | {speaker} | {text} |"
        )
    Path(f"{prefix}.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")


def relabel(args: argparse.Namespace) -> None:
    report = analyze_speakers(args)
    payload = json.loads(args.segments.read_text(encoding="utf-8"))
    payload["segments"] = relabel_segments(
        payload["segments"], report, max_nearest_ms=args.max_nearest_ms
    )
    metadata = dict(payload.get("metadata", {}))
    metadata["speaker_identity"] = {
        "profile": str(args.profile),
        "model": report["model"],
        "threshold": report["threshold"],
        "margin": report["margin"],
        "decisions": report["decisions"],
        "assignments": report["assignments"],
    }
    payload["metadata"] = metadata
    args.out_prefix.parent.mkdir(parents=True, exist_ok=True)
    Path(f"{args.out_prefix}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_transcript(args.out_prefix, payload)
    report_path = Path(f"{args.out_prefix}.speaker-id.json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(Path(f"{args.out_prefix}.json"))


def merge_profiles(args: argparse.Namespace) -> None:
    profiles = [json.loads(path.read_text(encoding="utf-8")) for path in args.profile]
    models = {profile["model"] for profile in profiles}
    if len(models) != 1:
        raise ValueError(f"profiles use different models: {sorted(models)}")

    identity_sets = [set(profile["identities"]) for profile in profiles]
    if any(identities != identity_sets[0] for identities in identity_sets[1:]):
        raise ValueError("profiles must contain the same identities")

    merged_identities: dict[str, list[list[float]]] = {}
    for identity in sorted(identity_sets[0]):
        prototypes = []
        for profile in profiles:
            prototypes.extend(profile_prototypes(profile["identities"][identity]))
        merged_identities[identity] = [prototype.tolist() for prototype in prototypes]

    calibrations = [profile.get("calibration", {}) for profile in profiles]
    thresholds = [
        float(calibration["suggested_threshold"])
        for calibration in calibrations
        if calibration.get("suggested_threshold") is not None
    ]
    margins = [
        float(calibration["suggested_margin"])
        for calibration in calibrations
        if calibration.get("suggested_margin") is not None
    ]
    merged = {
        "schema_version": 2,
        "model": models.pop(),
        "sample_rate": 16_000,
        "identities": merged_identities,
        "calibration": {
            "suggested_threshold": min(thresholds, default=0.65),
            "suggested_margin": max([0.05, *margins]),
        },
        "source_profiles": [str(path) for path in args.profile],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
    args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--audio", type=Path, required=True)
    common.add_argument("--segments", type=Path, required=True)
    common.add_argument("--device", default="cuda:0")
    common.add_argument("--model")
    common.add_argument("--minimum-ms", type=int, default=4_000)
    common.add_argument("--chunk-limit", type=int, default=12)
    common.add_argument("--timeline-limit", type=int, default=120)

    enroll_parser = subparsers.add_parser("enroll", parents=[common])
    enroll_parser.set_defaults(model="cam++")
    enroll_parser.add_argument("--label", action="append", required=True)
    enroll_parser.add_argument("--output", type=Path, required=True)
    enroll_parser.set_defaults(func=enroll)

    identify_parser = subparsers.add_parser("identify", parents=[common])
    identify_parser.add_argument("--profile", type=Path, required=True)
    identify_parser.add_argument("--threshold", type=float)
    identify_parser.add_argument("--margin", type=float)
    identify_parser.add_argument("--output", type=Path)
    identify_parser.set_defaults(func=identify)

    relabel_parser = subparsers.add_parser("relabel", parents=[common])
    relabel_parser.add_argument("--profile", type=Path, required=True)
    relabel_parser.add_argument("--threshold", type=float)
    relabel_parser.add_argument("--margin", type=float)
    relabel_parser.add_argument("--max-nearest-ms", type=int, default=30_000)
    relabel_parser.add_argument("--out-prefix", type=Path, required=True)
    relabel_parser.set_defaults(func=relabel)

    merge_parser = subparsers.add_parser("merge")
    merge_parser.add_argument("--profile", type=Path, action="append", required=True)
    merge_parser.add_argument("--output", type=Path, required=True)
    merge_parser.set_defaults(func=merge_profiles)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
