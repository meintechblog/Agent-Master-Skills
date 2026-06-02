#!/usr/bin/env bash
# chatgpt-image-restyle — generic Few-Shot restyle of a target image via ChatGPT.app.
#
# Usage: see SKILL.md (../SKILL.md) for full arg-list + examples.

set -euo pipefail

TARGET=""
STYLE_REFS_DIR=""
OUTPUT=""
OUTPUT_PNG=""
BACKGROUND=0
LOG=""
SOURCE_MODE="photo"      # photo | recipe-card | menu-shot
DIET=""                  # vegan | vegetarian | ""
MAIN_SUBJECTS=""
PRESERVE=""
PROMPT_OVERRIDE=""
RETRY_PROMPT_OVERRIDE=""
VERIFY_URL=""
MAX_RETRIES=1
NOTIFY_CMD=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)         TARGET="$2"; shift 2 ;;
    --style-refs)     STYLE_REFS_DIR="$2"; shift 2 ;;
    --output)         OUTPUT="$2"; shift 2 ;;
    --output-png)     OUTPUT_PNG="$2"; shift 2 ;;
    --background)     BACKGROUND=1; shift ;;
    --log)            LOG="$2"; shift 2 ;;
    --source-mode)    SOURCE_MODE="$2"; shift 2 ;;
    --diet)           DIET="$2"; shift 2 ;;
    --main-subjects)  MAIN_SUBJECTS="$2"; shift 2 ;;
    --preserve)       PRESERVE="$2"; shift 2 ;;
    --prompt)         PROMPT_OVERRIDE="$2"; shift 2 ;;
    --retry-prompt)   RETRY_PROMPT_OVERRIDE="$2"; shift 2 ;;
    --verify-url)     VERIFY_URL="$2"; shift 2 ;;
    --max-retries)    MAX_RETRIES="$2"; shift 2 ;;
    --notify)         NOTIFY_CMD="$2"; shift 2 ;;
    -h|--help)        sed -n '2,5p' "$0"; echo "See \$SKILL_DIR/SKILL.md for full docs."; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

[[ -z "$TARGET" || -z "$STYLE_REFS_DIR" || -z "$OUTPUT" ]] && {
  echo "Missing required arg(s): --target, --style-refs, --output are mandatory." >&2
  exit 2
}
[[ ! -f "$TARGET" ]] && { echo "Target not found: $TARGET" >&2; exit 2; }
[[ ! -d "$STYLE_REFS_DIR" ]] && { echo "Style-refs dir not found: $STYLE_REFS_DIR" >&2; exit 2; }

# Default log location: next to output
if [[ -z "$LOG" ]]; then
  LOG="${OUTPUT%.*}.restyle.log"
fi
SENTINEL="${OUTPUT}.done"

mkdir -p "$(dirname "$OUTPUT")"
[[ -n "$OUTPUT_PNG" ]] && mkdir -p "$(dirname "$OUTPUT_PNG")"
rm -f "$SENTINEL"

# ─── Background re-exec ────────────────────────────────────────────────────
if [[ "$BACKGROUND" -eq 1 ]]; then
  args=( --target "$TARGET" --style-refs "$STYLE_REFS_DIR" --output "$OUTPUT" --log "$LOG"
         --source-mode "$SOURCE_MODE" --max-retries "$MAX_RETRIES" )
  [[ -n "$OUTPUT_PNG" ]] && args+=( --output-png "$OUTPUT_PNG" )
  [[ -n "$DIET" ]] && args+=( --diet "$DIET" )
  [[ -n "$MAIN_SUBJECTS" ]] && args+=( --main-subjects "$MAIN_SUBJECTS" )
  [[ -n "$PRESERVE" ]] && args+=( --preserve "$PRESERVE" )
  [[ -n "$PROMPT_OVERRIDE" ]] && args+=( --prompt "$PROMPT_OVERRIDE" )
  [[ -n "$RETRY_PROMPT_OVERRIDE" ]] && args+=( --retry-prompt "$RETRY_PROMPT_OVERRIDE" )
  [[ -n "$VERIFY_URL" ]] && args+=( --verify-url "$VERIFY_URL" )
  [[ -n "$NOTIFY_CMD" ]] && args+=( --notify "$NOTIFY_CMD" )
  nohup bash "$0" "${args[@]}" > "$LOG" 2>&1 &
  echo "Restyle dispatched in background (pid=$!, log=$LOG)" >&2
  echo "$!" > "${OUTPUT}.pid"
  exit 0
fi

log() {
  local line="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
  echo "$line"
  [[ -n "$LOG" ]] && echo "$line" >> "$LOG"
}

# ─── Pre-flight ────────────────────────────────────────────────────────────
command -v cliclick >/dev/null || { log "ERROR cliclick missing — brew install cliclick"; exit 3; }
osascript -e 'tell application "ChatGPT" to get name' >/dev/null 2>&1 || {
  log "ERROR ChatGPT.app not installed/running"; exit 3
}

# Window-preflight: every step below references `window 1`. If ChatGPT has 0 windows
# (display locked / screensaver, or menubar/launcher-only mode → macOS materializes no
# window) those refs crash with AppleScript -1719. Try to materialize one; if it stays
# empty, abort cleanly with a diagnostic instead of a cryptic -1719 mid-run.
ensure_chatgpt_window() {
  local n
  n=$(osascript -e 'tell application "System Events" to tell process "ChatGPT" to count windows' 2>/dev/null)
  [[ "${n:-0}" -ge 1 ]] && return 0
  log "no ChatGPT window — trying to materialize one (activate + Ablage/File › Neuer Chat)"
  osascript >/dev/null 2>&1 <<'PF'
tell application "ChatGPT" to activate
delay 0.6
tell application "System Events" to tell process "ChatGPT"
  set frontmost to true
  delay 0.3
  try
    click menu item "Neuer Chat" of menu 1 of menu bar item "Ablage" of menu bar 1
  on error
    try
      click menu item "New Chat" of menu 1 of menu bar item "File" of menu bar 1
    end try
  end try
end tell
delay 1.0
PF
  n=$(osascript -e 'tell application "System Events" to tell process "ChatGPT" to count windows' 2>/dev/null)
  [[ "${n:-0}" -ge 1 ]]
}
ensure_chatgpt_window || {
  log "ERROR kein ChatGPT-Fenster: Display gesperrt/Screensaver oder menubar-only-Modus — Hintergrund-Agent kann kein Fenster materialisieren. Abbruch (statt AppleScript -1719)."; exit 3
}

style_files=()
shopt -s nullglob
for f in "$STYLE_REFS_DIR"/*.jpg "$STYLE_REFS_DIR"/*.JPG "$STYLE_REFS_DIR"/*.jpeg "$STYLE_REFS_DIR"/*.png "$STYLE_REFS_DIR"/*.PNG; do
  style_files+=( "$f" )
done
shopt -u nullglob
[[ ${#style_files[@]} -eq 0 ]] && { log "ERROR no style refs in $STYLE_REFS_DIR"; exit 3; }
# alphabetisch sortieren für deterministische Reihenfolge
IFS=$'\n' style_files=($(printf '%s\n' "${style_files[@]}" | sort))
unset IFS

log "starting restyle · target=$TARGET · style-refs=${#style_files[@]} · output=$OUTPUT"

# ─── 1. open new chat ──────────────────────────────────────────────────────
log "opening new ChatGPT chat"
osascript >/dev/null <<'EOF'
tell application "ChatGPT" to activate
delay 0.4
tell application "System Events"
  tell process "ChatGPT"
    set frontmost to true
    delay 0.2
    click button 2 of toolbar 1 of window 1
    delay 1.0
  end tell
end tell
EOF

# ─── 2. focus input ────────────────────────────────────────────────────────
osascript >/dev/null <<'EOF'
tell application "System Events"
  tell process "ChatGPT"
    set inputArea to UI element 1 of scroll area 3 of group 2 of splitter group 1 of group 1 of window 1
    click inputArea
    delay 0.5
  end tell
end tell
EOF

paste_img() {
  local p="$1"
  osascript -e "set the clipboard to (read POSIX file \"$p\" as JPEG picture)" >/dev/null 2>&1 || \
    osascript -e "set the clipboard to (read POSIX file \"$p\" as «class PNGf»)" >/dev/null
  sleep 0.3
  osascript -e 'tell application "System Events" to tell process "ChatGPT" to keystroke "v" using {command down}' >/dev/null
  sleep 2.0
}

for f in "${style_files[@]}"; do
  log "pasting style ref $(basename "$f")"
  paste_img "$f"
done
log "pasting target $(basename "$TARGET")"
paste_img "$TARGET"

# ─── 3. compose prompt ─────────────────────────────────────────────────────
compose_prompt() {
  local mode="$1"   # default | retry
  if [[ "$mode" == "default" && -n "$PROMPT_OVERRIDE" ]]; then
    printf '%s' "$PROMPT_OVERRIDE"; return
  fi
  if [[ "$mode" == "retry"   && -n "$RETRY_PROMPT_OVERRIDE" ]]; then
    printf '%s' "$RETRY_PROMPT_OVERRIDE"; return
  fi
  local n=${#style_files[@]}
  local refs_label
  if (( n == 1 )); then refs_label="Das erste Bild"
  elif (( n == 2 )); then refs_label="Die ersten zwei Bilder"
  elif (( n == 3 )); then refs_label="Die ersten drei Bilder"
  else refs_label="Die ersten $n Bilder"; fi
  local target_label="das vierte Bild"
  case $n in 1) target_label="das zweite Bild";; 2) target_label="das dritte Bild";; 4) target_label="das fünfte Bild";; esac

  local base
  if [[ "$mode" == "default" ]]; then
    case "$SOURCE_MODE" in
      recipe-card)
        base="$refs_label sind unser visueller Stil. $target_label ist eine abfotografierte Rezeptkarte mit Foto des fertigen Gerichts. Generiere ein neues Bild im Stil der ersten, das das Gericht von der Karte zeigt — alle Hauptzutaten drauf, aber mit einer natürlichen, leicht variierten Anordnung (nicht 1:1). Ignoriere das Karten-Layout, den Text und den Karten-Hintergrund."
        ;;
      menu-shot)
        base="$refs_label sind unser visueller Stil. $target_label ist ein Foto/Snapshot des Subjekts in seinem aktuellen Kontext. Generiere ein neues Bild im Stil der ersten, das das Subjekt isoliert + im Anker-Stil zeigt — natürliche Variation, kein 1:1-Copy."
        ;;
      *) # photo
        base="$refs_label sind unser visueller Stil. Generiere ein neues Bild im gleichen Stil, das dasselbe Subjekt wie $target_label zeigt — alle Hauptbestandteile drauf, aber mit einer natürlichen, leicht variierten Anordnung (nicht 1:1). Stil/Beleuchtung/Komposition folgen den Referenz-Bildern."
        ;;
    esac
  else
    base="Bitte nochmal — alle Hauptbestandteile aus $target_label drauf, aber natürlich-variiert (nicht 1:1). Stil exakt wie $refs_label."
  fi

  local diet_hint=""
  case "$DIET" in
    vegan)       diet_hint=" ⚠ Wichtig: das Subjekt ist VEGAN — KEINE Fleisch-/Hähnchen-/Hack-/Speckwürfel. Auch wenn der Name klassisch nach Fleisch klingt (Stroganoff/Bolognese/Carbonara o.ä.), sind ALLE proteinhaltigen Stücke pflanzlich." ;;
    vegetarian)  diet_hint=" Wichtig: vegetarisch — kein Fleisch / kein Fisch." ;;
  esac

  local main_hint=""
  [[ -n "$MAIN_SUBJECTS" ]] && main_hint=" Hauptbestandteile: $MAIN_SUBJECTS — müssen klar erkennbar im finalen Bild sein."

  local preserve_hint=""
  [[ -n "$PRESERVE" ]] && preserve_hint=" Erhalte unbedingt: $PRESERVE — diese Anker dürfen nicht weggelassen werden."

  printf '%s%s%s%s' "$base" "$diet_hint" "$main_hint" "$preserve_hint"
}

send_prompt() {
  local p="$1"
  local escaped
  escaped="$(printf '%s' "$p" | python3 -c 'import sys; print(sys.stdin.read().replace(chr(92), chr(92)*2).replace(chr(34), chr(92)+chr(34)), end="")')"
  osascript >/dev/null <<APPLE
tell application "System Events"
  tell process "ChatGPT"
    keystroke "$escaped"
    delay 0.6
    key code 36
  end tell
end tell
APPLE
}

PROMPT_TEXT="$(compose_prompt default)"
log "sending prompt (mode=$SOURCE_MODE, diet=${DIET:-none})"
send_prompt "$PROMPT_TEXT"

# ─── 4. poll for done ──────────────────────────────────────────────────────
poll_until_done() {
  local max=$1
  local elapsed=0
  while (( elapsed < max )); do
    local state
    state=$(osascript 2>/dev/null <<'EOF'
try
  tell application "System Events"
    tell process "ChatGPT"
      set imgArea to scroll area 1 of group 2 of splitter group 1 of group 1 of window 1
      try
        repeat with t in (static texts of imgArea)
          set v to value of t as text
          if v contains "Bild wird erstellt" or v contains "Generating image" then
            return "generating"
          end if
        end repeat
      end try
      try
        set outerList to list 1 of imgArea
        set innerList to list 1 of outerList
        repeat with bubble in (groups of innerList)
          try
            set innerGrp to group 1 of bubble
            set btn to button 1 of innerGrp
            set sz to size of btn
            if (item 2 of sz) > 400 then return "done"
          end try
        end repeat
      end try
      return "waiting"
    end tell
  end tell
on error
  return "error"
end try
EOF
)
    [[ "$state" == "done" ]] && return 0
    sleep 3
    elapsed=$((elapsed + 3))
  done
  return 1
}

log "polling for completion (max 120s)"
poll_until_done 120 && log "image generation done" || log "WARN polling timed out — proceeding anyway"

# ─── 5. locate latest image button + right-click ───────────────────────────
locate_latest_image() {
  osascript 2>/dev/null <<'EOF'
try
  tell application "System Events"
    tell process "ChatGPT"
      set imgArea to scroll area 1 of group 2 of splitter group 1 of group 1 of window 1
      set outerList to list 1 of imgArea
      set innerList to list 1 of outerList
      set foundBtn to missing value
      repeat with bubble in (groups of innerList)
        try
          set innerGrp to group 1 of bubble
          set btn to button 1 of innerGrp
          set sz to size of btn
          if (item 2 of sz) > 400 then set foundBtn to btn
        end try
      end repeat
      if foundBtn is missing value then return "0 0"
      set p to position of foundBtn
      set s to size of foundBtn
      return ((item 1 of p) + (item 1 of s) div 2) & " " & ((item 2 of p) + (item 2 of s) div 2)
    end tell
  end tell
on error
  return "0 0"
end try
EOF
}

extract_clipboard_png() {
  local out_png="$1"
  osascript >/dev/null <<APPLE
set imgData to the clipboard as «class PNGf»
set fd to open for access POSIX file "$out_png" with write permission
set eof of fd to 0
write imgData to fd
close access fd
APPLE
}

grab_latest_image() {
  local out_png="$1"
  read -r CX CY <<<"$(locate_latest_image)"
  [[ "$CX" == "0" ]] && { log "ERROR could not locate image button"; return 1; }
  log "right-clicking image center at ($CX, $CY)"
  osascript -e 'tell application "ChatGPT" to activate' >/dev/null
  sleep 0.3
  cliclick rc:"$CX","$CY"
  sleep 0.5
  osascript -e 'tell application "System Events" to key code 125' >/dev/null  # Down
  sleep 0.2
  osascript -e 'tell application "System Events" to key code 36' >/dev/null   # Return → "Bild kopieren"
  sleep 1.2
  extract_clipboard_png "$out_png"
  [[ -s "$out_png" ]] || { log "ERROR no PNG data in clipboard"; return 1; }
  return 0
}

# scratch PNG location
SCRATCH_PNG="${OUTPUT_PNG:-${OUTPUT%.*}.restyled.png}"
grab_latest_image "$SCRATCH_PNG" || exit 5
log "extracted clipboard PNG → $SCRATCH_PNG"

# ─── 6. PNG → JPEG q92 ─────────────────────────────────────────────────────
sips -s format jpeg -s formatOptions 92 "$SCRATCH_PNG" --out "$OUTPUT" >/dev/null
log "JPEG q92 ready at $OUTPUT"

# ─── 7. optional verify + retry loop ───────────────────────────────────────
if [[ -n "$VERIFY_URL" ]]; then
  VERIFY_SCRIPT=""
  for cand in \
    "$HOME/.claude/skills/cookidoo-recipe-publisher/scripts/verify-image-match.py" \
    "$HOME/.claude/skills/chatgpt-image-restyle/scripts/verify-image-match.py"
  do
    [[ -x "$cand" ]] && VERIFY_SCRIPT="$cand" && break
  done
  if [[ -z "$VERIFY_SCRIPT" ]]; then
    log "verify-url set but no verify-image-match.py found — skipping verify"
  else
    attempt=0
    while (( attempt < MAX_RETRIES + 1 )); do
      attempt=$((attempt + 1))
      log "verify attempt #$attempt against $VERIFY_URL"
      if "$VERIFY_SCRIPT" --user-image "$OUTPUT" --hf-url "$VERIFY_URL" >> "$LOG" 2>&1; then
        log "verify PASS"
        break
      fi
      rc=$?
      log "verify exit=$rc"
      if (( attempt > MAX_RETRIES )); then
        log "max retries reached, keeping last result"
        break
      fi
      # Retry-Prompt in selben Chat
      log "sending retry prompt"
      osascript >/dev/null <<'EOF'
tell application "System Events"
  tell process "ChatGPT"
    set inputArea to UI element 1 of scroll area 3 of group 2 of splitter group 1 of group 1 of window 1
    click inputArea
    delay 0.4
  end tell
end tell
EOF
      RETRY_TEXT="$(compose_prompt retry)"
      send_prompt "$RETRY_TEXT"
      poll_until_done 120 || log "WARN retry polling timed out"
      grab_latest_image "$SCRATCH_PNG" || { log "retry grab failed"; break; }
      sips -s format jpeg -s formatOptions 92 "$SCRATCH_PNG" --out "$OUTPUT" >/dev/null
      log "retry image saved"
    done
  fi
fi

# ─── 8. notify hook ────────────────────────────────────────────────────────
if [[ -n "$NOTIFY_CMD" ]]; then
  resolved="${NOTIFY_CMD//\{output\}/$OUTPUT}"
  resolved="${resolved//\{output_png\}/$SCRATCH_PNG}"
  resolved="${resolved//\{target\}/$TARGET}"
  log "notify: $resolved"
  bash -c "$resolved" >> "$LOG" 2>&1 || log "notify cmd exited non-zero (ignored)"
fi

# ─── 9. sentinel ───────────────────────────────────────────────────────────
touch "$SENTINEL"
log "DONE — sentinel touched at $SENTINEL"
