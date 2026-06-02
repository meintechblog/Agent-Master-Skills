#!/usr/bin/env bash
# transcribe.sh — YouTube → Markdown via yt-dlp + whisper.cpp
#
# Strategy:
#   1. Try YouTube auto-captions (fast, free)
#   2. Fallback to whisper.cpp medium if captions missing/poor
#
# Usage:
#   transcribe.sh <youtube-url|video-id|audio-file> [options]
#
# Options:
#   --output <path.md>      Output Markdown path (default: /tmp/transcripts/<id>.md)
#   --lang auto|de|en       Language (default: auto)
#   --model tiny|base|small|medium|large  Whisper model (default: medium)
#   --no-captions-first     Skip YouTube captions, always use Whisper
#   --background            Don't wait for completion
#   --keep-temp             Don't delete intermediate audio/vtt files

set -euo pipefail

MODEL_DIR="$HOME/.cache/whisper-cpp"
TRANSCRIPTS_DIR="${TRANSCRIPTS_DIR:-/tmp/transcripts}"
mkdir -p "$TRANSCRIPTS_DIR"

INPUT=""
OUTPUT=""
LANG="auto"
MODEL="medium"
SKIP_CAPTIONS=0
BACKGROUND=0
KEEP_TEMP=0

while [ $# -gt 0 ]; do
  case "$1" in
    --output) OUTPUT="$2"; shift 2 ;;
    --lang) LANG="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --no-captions-first) SKIP_CAPTIONS=1; shift ;;
    --background) BACKGROUND=1; shift ;;
    --keep-temp) KEEP_TEMP=1; shift ;;
    --help|-h)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -20
      exit 0
      ;;
    *)
      if [ -z "$INPUT" ]; then INPUT="$1"; fi
      shift
      ;;
  esac
done

if [ -z "$INPUT" ]; then
  echo "Usage: $0 <youtube-url|video-id|audio-file> [options]" >&2
  exit 1
fi

# ----- Parse Input -----
VIDEO_ID=""
SOURCE_URL=""
LOCAL_FILE=""

if [[ "$INPUT" =~ ^https?://(www\.)?(youtube\.com|youtu\.be|m\.youtube\.com) ]]; then
  # Extract video ID from URL
  if [[ "$INPUT" =~ v=([a-zA-Z0-9_-]{11}) ]]; then
    VIDEO_ID="${BASH_REMATCH[1]}"
  elif [[ "$INPUT" =~ youtu\.be/([a-zA-Z0-9_-]{11}) ]]; then
    VIDEO_ID="${BASH_REMATCH[1]}"
  elif [[ "$INPUT" =~ /shorts/([a-zA-Z0-9_-]{11}) ]]; then
    VIDEO_ID="${BASH_REMATCH[1]}"
  else
    echo "Could not extract video ID from URL: $INPUT" >&2; exit 1
  fi
  SOURCE_URL="https://youtu.be/$VIDEO_ID"
elif [[ "$INPUT" =~ ^[a-zA-Z0-9_-]{11}$ ]]; then
  VIDEO_ID="$INPUT"
  SOURCE_URL="https://youtu.be/$VIDEO_ID"
elif [ -f "$INPUT" ]; then
  LOCAL_FILE="$INPUT"
  VIDEO_ID="$(basename "$INPUT" | sed 's/\.[^.]*$//')"
  SOURCE_URL="file://$INPUT"
else
  echo "Input is neither YouTube URL/ID nor existing file: $INPUT" >&2; exit 1
fi

[ -z "$OUTPUT" ] && OUTPUT="$TRANSCRIPTS_DIR/$VIDEO_ID.md"

run_transcribe() {
  local tmpdir
  tmpdir="$(mktemp -d -t video-transcribe-XXXXXX)"
  trap "[ $KEEP_TEMP -eq 0 ] && rm -rf $tmpdir" EXIT

  local title="$VIDEO_ID"
  local channel=""
  local duration_s=""
  local detected_lang="$LANG"
  local method="unknown"
  local content=""

  # ----- Phase 1: Try YouTube auto-captions -----
  if [ -z "$LOCAL_FILE" ] && [ $SKIP_CAPTIONS -eq 0 ]; then
    echo "[1/3] Trying YouTube auto-captions for $VIDEO_ID..." >&2
    cd "$tmpdir"

    # Pull metadata + captions
    yt-dlp --skip-download \
      --write-auto-sub --write-sub --sub-lang "de,en" --sub-format vtt \
      --write-info-json \
      -o "%(id)s" \
      "$SOURCE_URL" >/dev/null 2>&1 || true

    # Read metadata
    if [ -f "$VIDEO_ID.info.json" ]; then
      title=$(python3 -c "import json; print(json.load(open('$VIDEO_ID.info.json')).get('title','$VIDEO_ID'))" 2>/dev/null || echo "$VIDEO_ID")
      channel=$(python3 -c "import json; print(json.load(open('$VIDEO_ID.info.json')).get('uploader',''))" 2>/dev/null || echo "")
      duration_s=$(python3 -c "import json; print(int(json.load(open('$VIDEO_ID.info.json')).get('duration',0)))" 2>/dev/null || echo "0")
    fi

    # Find a VTT file (prefer manual over auto, de over en)
    local vtt=""
    for ext in "de.vtt" "en.vtt" "de-orig.vtt" "en-orig.vtt"; do
      if [ -f "$VIDEO_ID.$ext" ]; then vtt="$VIDEO_ID.$ext"; break; fi
    done
    # Fallback: any vtt
    # NOTE: failing glob in cmdsub aborts the script under `set -euo pipefail`
    # (bash propagates the cmdsub exit when pipefail is on), so `|| true`.
    if [ -z "$vtt" ]; then
      vtt=$(ls "$VIDEO_ID."*.vtt 2>/dev/null | head -1 || true)
    fi

    if [ -n "$vtt" ] && [ -s "$vtt" ]; then
      # Quality check: > 60 % non-empty caption text
      local total_lines non_empty
      total_lines=$(grep -cv "^$\|-->\|^WEBVTT\|^Kind:\|^Language:" "$vtt" 2>/dev/null || echo 0)
      if [ "$total_lines" -ge 20 ]; then
        echo "    ✓ Captions found ($vtt, $total_lines lines)" >&2
        detected_lang=$(echo "$vtt" | sed -E 's/.*\.(de|en)[^.]*\.vtt/\1/')
        content=$(python3 "$(dirname "$0")/vtt_to_md.py" "$vtt" 2>&1)
        method="youtube-auto-captions"
      else
        echo "    × Captions too sparse ($total_lines lines), falling back to Whisper" >&2
      fi
    else
      echo "    × No captions available, falling back to Whisper" >&2
    fi
  fi

  # ----- Phase 2: Whisper fallback (or primary if --no-captions-first / local file) -----
  if [ -z "$content" ]; then
    echo "[2/3] Extracting audio for Whisper..." >&2
    local wav="$tmpdir/$VIDEO_ID.wav"

    if [ -n "$LOCAL_FILE" ]; then
      ffmpeg -hide_banner -loglevel error -y -i "$LOCAL_FILE" -ar 16000 -ac 1 -c:a pcm_s16le "$wav"
    else
      yt-dlp -x --audio-format wav --audio-quality 0 \
        --postprocessor-args "ffmpeg:-ar 16000 -ac 1" \
        -o "$tmpdir/$VIDEO_ID.%(ext)s" \
        "$SOURCE_URL" >"$tmpdir/yt-dlp.log" 2>&1 \
        || { echo "    × yt-dlp audio download failed, see $tmpdir/yt-dlp.log" >&2; cat "$tmpdir/yt-dlp.log" >&2; exit 4; }
      # yt-dlp output filename can differ; locate.
      # Note: yt-dlp typically writes "$VIDEO_ID.wav" (no mid-dot), so the
      # *.wav glob alone would miss it. List both patterns; `|| true` so a
      # failing-glob cmdsub doesn't silently abort under `set -euo pipefail`.
      wav=$(ls "$tmpdir/$VIDEO_ID.wav" "$tmpdir/$VIDEO_ID."*.wav 2>/dev/null | head -1 || true)
      # also get metadata if not already
      if [ -z "$title" ] || [ "$title" = "$VIDEO_ID" ]; then
        yt-dlp --skip-download --write-info-json -o "$tmpdir/%(id)s" "$SOURCE_URL" >/dev/null 2>&1 || true
        if [ -f "$tmpdir/$VIDEO_ID.info.json" ]; then
          title=$(python3 -c "import json; print(json.load(open('$tmpdir/$VIDEO_ID.info.json')).get('title','$VIDEO_ID'))" 2>/dev/null || echo "$VIDEO_ID")
          channel=$(python3 -c "import json; print(json.load(open('$tmpdir/$VIDEO_ID.info.json')).get('uploader',''))" 2>/dev/null || echo "")
          duration_s=$(python3 -c "import json; print(int(json.load(open('$tmpdir/$VIDEO_ID.info.json')).get('duration',0)))" 2>/dev/null || echo "0")
        fi
      fi
    fi

    if [ ! -s "$wav" ]; then
      echo "    × Audio extraction failed" >&2; exit 2
    fi

    echo "[3/3] Running whisper.cpp $MODEL on Metal GPU..." >&2
    local model_bin="$MODEL_DIR/ggml-$MODEL.bin"
    if [ ! -f "$model_bin" ]; then
      echo "    Pulling model $MODEL (one-time)..." >&2
      curl -L -# -o "$model_bin" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$MODEL.bin"
    fi

    local lang_arg=""
    [ "$LANG" != "auto" ] && lang_arg="-l $LANG"

    whisper-cli -m "$model_bin" -t 8 -p 1 $lang_arg \
      -ovtt -of "$tmpdir/$VIDEO_ID" \
      -f "$wav" 2>&1 | tail -5 >&2

    local vtt="$tmpdir/$VIDEO_ID.vtt"
    if [ ! -s "$vtt" ]; then
      echo "    × Whisper failed to produce VTT" >&2; exit 3
    fi

    content=$(python3 "$(dirname "$0")/vtt_to_md.py" "$vtt")
    method="whisper-$MODEL"
    detected_lang=$([ "$LANG" = "auto" ] && echo "auto-detected" || echo "$LANG")
  fi

  # ----- Build final Markdown -----
  mkdir -p "$(dirname "$OUTPUT")"
  cat > "$OUTPUT" <<EOF
---
title: "$title"
source_url: $SOURCE_URL
video_id: $VIDEO_ID
channel: "$channel"
duration_s: $duration_s
language: $detected_lang
method: $method
transcribed_at: $(date -u +%Y-%m-%d)
---

# $title

> Quelle: [$SOURCE_URL]($SOURCE_URL) — transkribiert $(date +%Y-%m-%d) via $method

## Transcript

$content
EOF

  echo "✓ $OUTPUT  ($(wc -l < "$OUTPUT") lines, method=$method)" >&2
}

if [ $BACKGROUND -eq 1 ]; then
  ( run_transcribe ) >/tmp/transcribe-$VIDEO_ID.log 2>&1 &
  echo "Background PID: $!  log: /tmp/transcribe-$VIDEO_ID.log  output: $OUTPUT"
else
  run_transcribe
fi
