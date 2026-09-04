#!/usr/bin/env python3
"""Enroll and evaluate closed-set speaker identities from diarized meetings."""

from __future__ import annotations

import argparse
import itertools
import json
import math
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
) -> list[np.ndarray]:
    samples = [
        load_audio_chunk(
            audio_path,
            max(0, int(chunk["start_ms"])),
            int(chunk["end_ms"]),
        )
        for chunk in chunks
    ]
    if not samples:
        return []

    results = model.generate(input=samples, cache={}, is_final=True)
    embeddings = []
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
) -> tuple[dict[str, list[np.ndarray]], dict[str, list[dict[str, Any]]]]:
    from funasr import AutoModel

    grouped = merge_speaker_segments(load_segments(segments_path))
    selected = {
        speaker: select_chunks(chunks, minimum_ms=minimum_ms, limit=chunk_limit)
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


def identify(args: argparse.Namespace) -> None:
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
        chunk_limit=args.chunk_limit,
    )
    speaker_centroids = {
        speaker: robust_centroid(items)[0] for speaker, items in embeddings.items()
    }
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
            chunk_scores = {
                name: max(
                    float(normalize(embedding) @ prototype)
                    for prototype in prototypes
                )
                for name, prototypes in identities.items()
            }
            ranked = sorted(chunk_scores, key=chunk_scores.get, reverse=True)
            best_name = ranked[0]
            best_score = chunk_scores[best_name]
            runner_up_score = chunk_scores[ranked[1]] if len(ranked) > 1 else -1.0
            accepted = best_score >= threshold and best_score - runner_up_score >= margin
            evidence.append(
                {
                    "range_ms": [int(chunk["start_ms"]), int(chunk["end_ms"])],
                    "identity": best_name if accepted else "UNKNOWN",
                    "score": round(best_score, 6),
                    "margin": round(best_score - runner_up_score, 6),
                    "scores": {
                        name: round(score, 6) for name, score in chunk_scores.items()
                    },
                }
            )
        chunk_evidence[speaker] = evidence

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
        "chunk_evidence": chunk_evidence,
        "selected_chunks": {
            speaker: [
                [int(chunk["start_ms"]), int(chunk["end_ms"])] for chunk in speaker_chunks
            ]
            for speaker, speaker_chunks in chunks.items()
        },
    }
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")


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
    common.add_argument("--model", default="cam++")
    common.add_argument("--minimum-ms", type=int, default=4_000)
    common.add_argument("--chunk-limit", type=int, default=12)

    enroll_parser = subparsers.add_parser("enroll", parents=[common])
    enroll_parser.add_argument("--label", action="append", required=True)
    enroll_parser.add_argument("--output", type=Path, required=True)
    enroll_parser.set_defaults(func=enroll)

    identify_parser = subparsers.add_parser("identify", parents=[common])
    identify_parser.add_argument("--profile", type=Path, required=True)
    identify_parser.add_argument("--threshold", type=float)
    identify_parser.add_argument("--margin", type=float)
    identify_parser.add_argument("--output", type=Path)
    identify_parser.set_defaults(func=identify)

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
