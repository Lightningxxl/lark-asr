#!/usr/bin/env bash
set -uo pipefail

usage() {
  echo "usage: asr_fallback.sh --input AUDIO --output-dir DIR [--minute-token TOKEN]" >&2
}

INPUT=""
OUT_DIR=""
MINUTE_TOKEN=""

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
WHISPER_MODEL="${LARK_ASR_WHISPER_MODEL:-large-v3}"
WHISPER_MODEL_DIR="${LARK_ASR_WHISPER_MODEL_DIR:-}"
COMPUTE_TYPE="${LARK_ASR_COMPUTE_TYPE:-float16}"
USE_WHISPER="${LARK_ASR_USE_WHISPER:-1}"

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
  if "$PYTHON" "${whisper_args[@]}"; then
    whisper_ok=1
  else
    echo "warning: Whisper failed; falling back to FunASR transcript if available" >&2
  fi
fi

whisper_json="$WHISPER_DIR/$STEM.large-v3.json"
funasr_json="$FUNASR_DIR/$STEM.funasr.json"

if [[ "$whisper_ok" == "1" && "$funasr_ok" == "1" && -f "$whisper_json" && -f "$funasr_json" ]]; then
  "$PYTHON" "$SCRIPT_DIR/label_whisper_with_speakers.py" \
    --whisper-json "$whisper_json" \
    --speaker-json "$funasr_json" \
    --out-prefix "$FINAL_PREFIX"
  echo "$FINAL_PREFIX.md"
  exit 0
fi

if [[ "$funasr_ok" == "1" && -f "$FUNASR_DIR/$STEM.funasr.md" ]]; then
  cp "$FUNASR_DIR/$STEM.funasr.md" "$FINAL_PREFIX.md"
  cp "$FUNASR_DIR/$STEM.funasr.txt" "$FINAL_PREFIX.txt" 2>/dev/null || true
  cp "$FUNASR_DIR/$STEM.funasr.json" "$FINAL_PREFIX.json" 2>/dev/null || true
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
