#!/usr/bin/env bash
set -uo pipefail

usage() {
  echo "usage: asr_fallback.sh --input AUDIO --output-dir DIR [--minute-token TOKEN] [--terminology-file FILE]" >&2
}

INPUT=""
OUT_DIR=""
MINUTE_TOKEN=""
TERMINOLOGY_FILE="${LARK_ASR_TERMINOLOGY_FILE:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input)
      INPUT="${2:-}"
      shift 2
      ;;
    --output-dir|--out-dir)
      OUT_DIR="${2:-}"
      shift 2
      ;;
    --minute-token)
      MINUTE_TOKEN="${2:-}"
      shift 2
      ;;
    --terminology-file)
      TERMINOLOGY_FILE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$INPUT" || -z "$OUT_DIR" ]]; then
  usage
  exit 2
fi

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${LARK_ASR_PYTHON:-python3}"
DEVICE="${LARK_ASR_DEVICE:-cuda}"
FUNASR_DEVICE="${LARK_ASR_FUNASR_DEVICE:-cuda:0}"
FUNASR_MODEL="${LARK_ASR_FUNASR_MODEL:-iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch}"
FUNASR_VAD_MODEL="${LARK_ASR_FUNASR_VAD_MODEL:-fsmn-vad}"
FUNASR_PUNC_MODEL="${LARK_ASR_FUNASR_PUNC_MODEL:-ct-punc}"
FUNASR_SPK_MODEL="${LARK_ASR_FUNASR_SPK_MODEL:-cam++}"
SPEAKER_PROFILE="${LARK_ASR_SPEAKER_PROFILE:-}"
SPEAKER_ID_DEVICE="${LARK_ASR_SPEAKER_ID_DEVICE:-$FUNASR_DEVICE}"
SPEAKER_ID_THRESHOLD="${LARK_ASR_SPEAKER_ID_THRESHOLD:-}"
SPEAKER_ID_MARGIN="${LARK_ASR_SPEAKER_ID_MARGIN:-}"
WHISPER_MODEL="${LARK_ASR_WHISPER_MODEL:-large-v3}"
WHISPER_MODEL_DIR="${LARK_ASR_WHISPER_MODEL_DIR:-}"
COMPUTE_TYPE="${LARK_ASR_COMPUTE_TYPE:-float16}"
USE_WHISPER="${LARK_ASR_USE_WHISPER:-1}"
RESTORE_PUNCTUATION="${LARK_ASR_RESTORE_PUNCTUATION:-1}"
SPEAKER_MAX_GAP_MS="${LARK_ASR_SPEAKER_MAX_GAP_MS:-1200}"
SPEAKER_MAX_SEGMENT_MS="${LARK_ASR_SPEAKER_MAX_SEGMENT_MS:-30000}"
SPEAKER_MAX_SEGMENT_CHARS="${LARK_ASR_SPEAKER_MAX_SEGMENT_CHARS:-180}"

mkdir -p "$OUT_DIR"

FUNASR_DIR="$OUT_DIR/funasr"
WHISPER_DIR="$OUT_DIR/whisper"
FINAL_PREFIX="$OUT_DIR/transcript"
STEM="$(basename "$INPUT")"
STEM="${STEM%.*}"

echo "input=$INPUT"
echo "output_dir=$OUT_DIR"
echo "minute_token=$MINUTE_TOKEN"

funasr_ok=0
mkdir -p "$FUNASR_DIR"
if "$PYTHON" "$SCRIPT_DIR/transcribe_funasr.py" "$INPUT" \
  --out-dir "$FUNASR_DIR" \
  --model "$FUNASR_MODEL" \
  --vad-model "$FUNASR_VAD_MODEL" \
  --punc-model "$FUNASR_PUNC_MODEL" \
  --spk-model "$FUNASR_SPK_MODEL" \
  --device "$FUNASR_DEVICE"; then
  funasr_ok=1
else
  echo "warning: FunASR failed; continuing if Whisper can produce text" >&2
fi

funasr_json="$FUNASR_DIR/$STEM.funasr.json"
speaker_json="$funasr_json"
speaker_funasr_prefix="$FUNASR_DIR/$STEM.funasr.identified"
if [[ "$funasr_ok" == "1" && -n "$SPEAKER_PROFILE" ]]; then
  if [[ ! -f "$SPEAKER_PROFILE" ]]; then
    echo "speaker profile does not exist: $SPEAKER_PROFILE" >&2
    exit 1
  fi
  speaker_id_args=(
    "$SCRIPT_DIR/identify_speakers.py"
    relabel
    --audio "$INPUT"
    --segments "$funasr_json"
    --profile "$SPEAKER_PROFILE"
    --device "$SPEAKER_ID_DEVICE"
    --out-prefix "$speaker_funasr_prefix"
  )
  if [[ -n "$SPEAKER_ID_THRESHOLD" ]]; then
    speaker_id_args+=(--threshold "$SPEAKER_ID_THRESHOLD")
  fi
  if [[ -n "$SPEAKER_ID_MARGIN" ]]; then
    speaker_id_args+=(--margin "$SPEAKER_ID_MARGIN")
  fi
  if ! "$PYTHON" "${speaker_id_args[@]}"; then
    echo "speaker identification failed" >&2
    exit 1
  fi
  speaker_json="$speaker_funasr_prefix.json"
fi

whisper_ok=0
if [[ "$USE_WHISPER" == "1" || "$USE_WHISPER" == "true" ]]; then
  mkdir -p "$WHISPER_DIR"
  whisper_args=(
    "$SCRIPT_DIR/transcribe_faster_whisper.py"
    "$INPUT"
    --out-dir "$WHISPER_DIR"
    --model "$WHISPER_MODEL"
    --device "$DEVICE"
    --compute-type "$COMPUTE_TYPE"
  )
  if [[ -n "$WHISPER_MODEL_DIR" ]]; then
    whisper_args+=(--model-dir "$WHISPER_MODEL_DIR")
  fi
  if [[ -n "$TERMINOLOGY_FILE" ]]; then
    whisper_args+=(--terminology-file "$TERMINOLOGY_FILE")
  fi
  if "$PYTHON" "${whisper_args[@]}"; then
    whisper_ok=1
  else
    echo "warning: Whisper failed; falling back to FunASR transcript if available" >&2
  fi
fi

whisper_json="$WHISPER_DIR/$STEM.large-v3.json"

if [[ "$whisper_ok" == "1" && "$funasr_ok" == "1" && -f "$whisper_json" && -f "$speaker_json" ]]; then
  speaker_prefix="$OUT_DIR/transcript.speakers"
  "$PYTHON" "$SCRIPT_DIR/label_whisper_with_speakers.py" \
    --whisper-json "$whisper_json" \
    --speaker-json "$speaker_json" \
    --out-prefix "$speaker_prefix" \
    --max-gap-ms "$SPEAKER_MAX_GAP_MS" \
    --max-segment-ms "$SPEAKER_MAX_SEGMENT_MS" \
    --max-segment-chars "$SPEAKER_MAX_SEGMENT_CHARS"
  if [[ "$RESTORE_PUNCTUATION" == "1" || "$RESTORE_PUNCTUATION" == "true" ]]; then
    if "$PYTHON" "$SCRIPT_DIR/restore_punctuation_funasr.py" \
      --input-json "$speaker_prefix.json" \
      --out-prefix "$FINAL_PREFIX" \
      --model "$FUNASR_PUNC_MODEL" \
      --device "$FUNASR_DEVICE"; then
      echo "$FINAL_PREFIX.md"
      exit 0
    fi
    echo "warning: punctuation restoration failed; using speaker-labeled Whisper transcript" >&2
  fi
  cp "$speaker_prefix.md" "$FINAL_PREFIX.md"
  cp "$speaker_prefix.txt" "$FINAL_PREFIX.txt" 2>/dev/null || true
  cp "$speaker_prefix.json" "$FINAL_PREFIX.json" 2>/dev/null || true
  echo "$FINAL_PREFIX.md"
  exit 0
fi

if [[ "$funasr_ok" == "1" && -f "${speaker_json%.json}.md" ]]; then
  cp "${speaker_json%.json}.md" "$FINAL_PREFIX.md"
  cp "${speaker_json%.json}.txt" "$FINAL_PREFIX.txt" 2>/dev/null || true
  cp "$speaker_json" "$FINAL_PREFIX.json" 2>/dev/null || true
  echo "$FINAL_PREFIX.md"
  exit 0
fi

if [[ "$whisper_ok" == "1" && -f "$WHISPER_DIR/$STEM.large-v3.md" ]]; then
  cp "$WHISPER_DIR/$STEM.large-v3.md" "$FINAL_PREFIX.md"
  cp "$WHISPER_DIR/$STEM.large-v3.txt" "$FINAL_PREFIX.txt" 2>/dev/null || true
  cp "$WHISPER_DIR/$STEM.large-v3.json" "$FINAL_PREFIX.json" 2>/dev/null || true
  echo "$FINAL_PREFIX.md"
  exit 0
fi

echo "no ASR transcript was produced" >&2
exit 1
