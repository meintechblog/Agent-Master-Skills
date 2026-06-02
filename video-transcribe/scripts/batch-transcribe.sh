#!/usr/bin/env bash
# batch-transcribe.sh — Transkribiert eine Liste von YT-URLs/IDs in einen Output-Dir.
#
# Usage:
#   batch-transcribe.sh <urls-file> <output-dir> [--lang auto|de|en] [--model medium|small] [--parallel N] [--no-captions-first] [--keep-temp]
#
# URLs-File: eine YouTube-URL/ID pro Zeile, # = Kommentar
# Output-Dir: pro Video eine <id>.md
# Parallel: gleichzeitige Whisper-Runs. Default 1 (Apple Silicon = besser einer auf einmal mit allen Cores).

set -euo pipefail

URLS_FILE=""
OUT_DIR=""
LANG="auto"
MODEL="medium"
PARALLEL=1
EXTRA_FLAGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --lang) LANG="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --parallel) PARALLEL="$2"; shift 2 ;;
    --no-captions-first|--keep-temp) EXTRA_FLAGS+=("$1"); shift ;;
    --help|-h)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -12
      exit 0
      ;;
    *)
      if [ -z "$URLS_FILE" ]; then URLS_FILE="$1"
      elif [ -z "$OUT_DIR" ]; then OUT_DIR="$1"
      fi
      shift
      ;;
  esac
done

[ -z "$URLS_FILE" ] && { echo "Usage: $0 <urls-file> <output-dir> [opts]"; exit 1; }
[ -z "$OUT_DIR" ] && { echo "Usage: $0 <urls-file> <output-dir> [opts]"; exit 1; }
[ ! -f "$URLS_FILE" ] && { echo "URLs file not found: $URLS_FILE"; exit 2; }
mkdir -p "$OUT_DIR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRANSCRIBE="$SCRIPT_DIR/transcribe.sh"

TOTAL=$(grep -cv '^#\|^$' "$URLS_FILE" || echo 0)
echo "Batch transcribing $TOTAL items from $URLS_FILE → $OUT_DIR"
echo "Model: $MODEL  Lang: $LANG  Parallel: $PARALLEL"
echo ""

i=0
while IFS= read -r url; do
  url=$(echo "$url" | tr -d ' \t\r')
  [ -z "$url" ] && continue
  [[ "$url" =~ ^# ]] && continue
  i=$((i+1))

  # Extract ID for filename + skip if already done
  if [[ "$url" =~ v=([a-zA-Z0-9_-]{11}) ]]; then
    vid="${BASH_REMATCH[1]}"
  elif [[ "$url" =~ youtu\.be/([a-zA-Z0-9_-]{11}) ]]; then
    vid="${BASH_REMATCH[1]}"
  elif [[ "$url" =~ ^[a-zA-Z0-9_-]{11}$ ]]; then
    vid="$url"
  else
    vid="item-$i"
  fi

  out="$OUT_DIR/$vid.md"
  if [ -s "$out" ]; then
    echo "[$i/$TOTAL] SKIP $vid (already exists: $out)"
    continue
  fi

  echo "[$i/$TOTAL] $vid"
  "$TRANSCRIBE" "$url" --output "$out" --lang "$LANG" --model "$MODEL" "${EXTRA_FLAGS[@]}" || echo "  ! failed"

  # Throttle: pause 1s between videos to avoid rate-limits from yt-dlp
  sleep 1
done < "$URLS_FILE"

echo ""
echo "Done. $i transcripts attempted in $OUT_DIR"
ls -la "$OUT_DIR" | head -5
