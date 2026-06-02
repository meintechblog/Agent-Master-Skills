#!/usr/bin/env bash
# DEPRECATED seit 2026-05-27 — die generische Logik wurde nach
# ~/.claude/skills/chatgpt-image-restyle/scripts/restyle.sh extrahiert.
# Dieses Script bleibt aus historischen Gründen, neue Aufrufer sollten
# direkt den User-Skill verwenden. Siehe ~/.claude/skills/chatgpt-image-restyle/SKILL.md.
#
# ChatGPT-Restyle Pipeline mit Polling, Background-Mode, Verify-Loop, optional Notify.
#
# Usage:
#   chatgpt-restyle.sh \
#     --target /path/to/hf-original.jpg \
#     --slug   sweet-chili-bowl \
#     --nr     33 \
#     --repo   ~/codex/cookidoo-recipe-publisher \
#     [--hf-url <url>] [--no-notify] [--background]
#
# Output (sequential mode): writes restyled JPEG to .received/hf<NR>/restyled-final.jpg
#                          + copy at recipes/<slug>/hero.jpg
# Output (background mode): same, but exits immediately with PID in stderr;
#                          caller polls .received/hf<NR>/.done sentinel.

set -euo pipefail

TARGET=""
SLUG=""
NR=""
REPO=""
HF_URL=""
NOTIFY=1
BACKGROUND=0
CARD_MODE=0
DIET=""
MAIN_INGREDIENTS=""
GARNISH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)            TARGET="$2"; shift 2 ;;
    --slug)              SLUG="$2"; shift 2 ;;
    --nr)                NR="$2"; shift 2 ;;
    --repo)              REPO="$2"; shift 2 ;;
    --hf-url)            HF_URL="$2"; shift 2 ;;
    --no-notify)         NOTIFY=0; shift ;;
    --background)        BACKGROUND=1; shift ;;
    --card-mode)         CARD_MODE=1; shift ;;
    --diet)              DIET="$2"; shift 2 ;;             # "vegan" | "vegetarisch" | ""
    --main-ingredients)  MAIN_INGREDIENTS="$2"; shift 2 ;; # short list, comma-sep
    --garnish)           GARNISH="$2"; shift 2 ;;          # "Zitronenkeile, Kräuter, ..."
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

[[ -z "$TARGET" || -z "$SLUG" || -z "$NR" || -z "$REPO" ]] && {
  echo "Missing required arg(s)" >&2
  exit 2
}

RECEIVED="$REPO/.received/hf$NR"
STYLE_DIR="$REPO/style-references"
RECIPE_DIR="$REPO/recipes/$SLUG"
LOG="$RECEIVED/restyle.log"
DONE="$RECEIVED/.done"

mkdir -p "$RECEIVED" "$RECIPE_DIR"
rm -f "$DONE"

# When backgrounded, re-exec self in disowned subshell so caller can return.
if [[ "$BACKGROUND" -eq 1 ]]; then
  nohup bash "$0" \
    --target "$TARGET" \
    --slug "$SLUG" \
    --nr "$NR" \
    --repo "$REPO" \
    ${HF_URL:+--hf-url "$HF_URL"} \
    ${DIET:+--diet "$DIET"} \
    ${MAIN_INGREDIENTS:+--main-ingredients "$MAIN_INGREDIENTS"} \
    ${GARNISH:+--garnish "$GARNISH"} \
    $([ "$NOTIFY" -eq 0 ] && echo "--no-notify") \
    $([ "$CARD_MODE" -eq 1 ] && echo "--card-mode") \
    > "$LOG" 2>&1 &
  BG_PID=$!
  echo "$BG_PID" > "$RECEIVED/.pid"
  echo "Restyle dispatched in background (pid=$BG_PID, log=$LOG)" >&2
  exit 0
fi

# ─── from here: foreground execution ───────────────────────────────────────

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

# Save HF original into .received/ (skip if target already lives there — common case)
if [[ "$(cd "$(dirname "$TARGET")" && pwd)/$(basename "$TARGET")" != "$RECEIVED/original.jpg" ]]; then
  cp "$TARGET" "$RECEIVED/original.jpg"
fi
log "HF original ready at $RECEIVED/original.jpg"

# Confirm prerequisites
command -v cliclick >/dev/null || { log "ERROR cliclick missing — brew install cliclick"; exit 3; }
osascript -e 'tell application "ChatGPT" to get name' >/dev/null 2>&1 || {
  log "ERROR ChatGPT.app not installed/running"; exit 3
}

# ─── 1. open new chat ───────────────────────────────────────────────────────
log "opening new ChatGPT chat"
osascript <<'EOF' >/dev/null
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

# ─── 2. focus message input ─────────────────────────────────────────────────
osascript <<'EOF' >/dev/null
tell application "System Events"
  tell process "ChatGPT"
    set inputArea to UI element 1 of scroll area 3 of group 2 of splitter group 1 of group 1 of window 1
    click inputArea
    delay 0.5
  end tell
end tell
EOF

# ─── 3. paste 3 style references + target ───────────────────────────────────
paste_img() {
  local p="$1"
  osascript -e "set the clipboard to (read POSIX file \"$p\" as JPEG picture)" >/dev/null
  sleep 0.3
  osascript -e 'tell application "System Events" to tell process "ChatGPT" to keystroke "v" using {command down}' >/dev/null
  sleep 2.0
}

for f in "$STYLE_DIR"/*.jpg; do
  log "pasting style reference $(basename "$f")"
  paste_img "$f"
done
log "pasting target $(basename "$TARGET")"
paste_img "$TARGET"

# ─── 4. type prompt + send ──────────────────────────────────────────────────
# Build diet-hint paragraph (Vegan-Bias defense for "Stroganoff/Bolognese/Carbonara/..." gerichte)
DIET_HINT=""
if [[ -n "$DIET" ]]; then
  case "$DIET" in
    vegan)
      DIET_HINT=" ⚠ Wichtig: das Rezept ist VEGAN — KEINE Fleisch-/Hähnchen-/Hack-/Speckwürfel. Auch wenn der Gerichtsname klassisch nach Fleisch klingt (Stroganoff/Bolognese/Carbonara o.ä.), sind ALLE proteinhaltigen Stücke pflanzlich."
      ;;
    vegetarisch)
      DIET_HINT=" Wichtig: das Rezept ist vegetarisch — kein Fleisch / kein Fisch."
      ;;
  esac
fi

MAIN_HINT=""
[[ -n "$MAIN_INGREDIENTS" ]] && MAIN_HINT=" Die Hauptbestandteile sind: $MAIN_INGREDIENTS — diese müssen klar erkennbar im finalen Bild sein."

GARNISH_HINT=""
[[ -n "$GARNISH" ]] && GARNISH_HINT=" Garnitur-Anker: $GARNISH — diese visuellen Akzente sollen drauf bleiben."

if [[ "$CARD_MODE" -eq 1 ]]; then
  PROMPT="Die ersten drei Bilder sind unser visueller Stil für die Thermomix-App (warmes Licht, professionelle Food-Fotografie, blauer Tellerrand-Akzent, Top-Down-Perspektive). Das vierte Bild ist eine abfotografierte HelloFresh-Rezeptkarte mit einem Foto des fertigen Gerichts darauf. Generiere mir ein neues Bild im Stil der ersten drei, das das Gericht von der Karte zeigt — alle Hauptzutaten müssen drauf sein, aber mit einer natürlichen, leicht variierten Anordnung (nicht 1:1 wie auf der Karte). Das Gericht soll authentisch wirken, als wäre es gerade frisch angerichtet worden, nicht wie eine exakte Reproduktion. Ignoriere das Karten-Layout, den Text und den Karten-Hintergrund.${DIET_HINT}${MAIN_HINT}${GARNISH_HINT}"
  RETRY_PROMPT="Bitte nochmal — alle Hauptzutaten der fotografierten Karte drauf, aber natürlich-variiert angerichtet (nicht 1:1). Stil wie in den ersten drei Bildern (blauer Teller, Top-Down, warmes Licht).${DIET_HINT}${MAIN_HINT}${GARNISH_HINT}"
else
  PROMPT="Die ersten drei Bilder sind unser visueller Stil für die Thermomix-App (warmes Licht, professionelle Food-Fotografie, blauer Tellerrand-Akzent, Top-Down-Perspektive). Generiere mir ein neues Bild im gleichen Stil, das dasselbe Gericht wie das vierte Bild zeigt — alle Hauptzutaten müssen drauf sein, aber mit einer natürlichen, leicht variierten Anordnung (nicht 1:1 wie das Original). Das Gericht soll authentisch wirken, als wäre es gerade frisch angerichtet worden, nicht wie eine exakte Reproduktion. Stil/Beleuchtung/Tellerwahl folgen den ersten drei Bildern.${DIET_HINT}${MAIN_HINT}${GARNISH_HINT}"
  RETRY_PROMPT="Bitte nochmal — alle Hauptzutaten des 4. Bilds müssen drin sein, aber natürlich-variiert angerichtet (nicht 1:1). Stil wie in den ersten drei Bildern (blauer Teller, Top-Down, warmes Licht).${DIET_HINT}${MAIN_HINT}${GARNISH_HINT}"
fi

send_prompt() {
  local p="$1"
  osascript <<APPLE >/dev/null
tell application "System Events"
  tell process "ChatGPT"
    keystroke "$(echo "$p" | sed 's/"/\\"/g')"
    delay 0.6
    key code 36
  end tell
end tell
APPLE
}

log "sending prompt"
send_prompt "$PROMPT"

# ─── 5. poll for completion ─────────────────────────────────────────────────
poll_until_done() {
  local max=$1   # seconds
  local elapsed=0
  while (( elapsed < max )); do
    # Heuristic: if no "Bild wird erstellt" AXStaticText is visible AND there's
    # an image button > 400px tall in the main pane → generation done.
    local state
    state=$(osascript <<'EOF' 2>/dev/null
try
  tell application "System Events"
    tell process "ChatGPT"
      set mainG to group 2 of splitter group 1 of group 1 of window 1
      set imgArea to scroll area 1 of mainG
      -- "Bild wird erstellt" / "Generating image" → still rendering
      try
        repeat with t in (static texts of imgArea)
          set v to value of t as text
          if v contains "Bild wird erstellt" or v contains "Generating image" then
            return "generating"
          end if
        end repeat
      end try
      -- AI image lives at: scroll area 1 > list 1 > list 1 > group N > group 1 > button 1, size 866x437
      -- Iterate all chat-bubble groups, look inside each for a tall AXButton.
      try
        set outerList to list 1 of imgArea
        set innerList to list 1 of outerList
        repeat with bubble in (groups of innerList)
          try
            set innerGrp to group 1 of bubble
            try
              set btn to button 1 of innerGrp
              set sz to size of btn
              if (item 2 of sz) > 400 then return "done"
            end try
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
    case "$state" in
      done) return 0 ;;
      generating|waiting) ;;
      *) ;;
    esac
    sleep 3
    elapsed=$((elapsed + 3))
  done
  return 1
}

log "polling for completion (max 120s)"
if poll_until_done 120; then
  log "image generation done after ${elapsed:-?}s"
else
  log "WARN polling timed out — proceeding anyway"
fi

# ─── 6. right-click image + copy ────────────────────────────────────────────
# Compute image center from AX tree
read -r CX CY <<<"$(osascript <<'EOF' 2>/dev/null
try
  tell application "System Events"
    tell process "ChatGPT"
      set mainG to group 2 of splitter group 1 of group 1 of window 1
      set outerList to list 1 of scroll area 1 of mainG
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
)"
[[ "$CX" == "0" ]] && { log "ERROR could not locate image button"; exit 4; }
log "right-clicking image center at ($CX, $CY)"
osascript -e 'tell application "ChatGPT" to activate' >/dev/null
sleep 0.3
cliclick rc:$CX,$CY
sleep 0.5
osascript -e 'tell application "System Events" to key code 125' >/dev/null  # Down
sleep 0.2
osascript -e 'tell application "System Events" to key code 36' >/dev/null   # Return
sleep 1.2

# ─── 7. extract clipboard PNG ───────────────────────────────────────────────
osascript <<APPLE >/dev/null
set imgData to the clipboard as «class PNGf»
set fd to open for access POSIX file "$RECEIVED/restyled-fullres.png" with write permission
set eof of fd to 0
write imgData to fd
close access fd
APPLE

if [[ ! -s "$RECEIVED/restyled-fullres.png" ]]; then
  log "ERROR no image in clipboard"; exit 5
fi
log "extracted clipboard PNG → restyled-fullres.png"

# ─── 8. convert to JPEG q92 + copy to recipe ───────────────────────────────
sips -s format jpeg -s formatOptions 92 \
  "$RECEIVED/restyled-fullres.png" \
  --out "$RECEIVED/restyled-final.jpg" >/dev/null
cp "$RECEIVED/restyled-final.jpg" "$RECIPE_DIR/hero.jpg"
log "JPEG q92 ready at $RECIPE_DIR/hero.jpg"

# ─── 9. auto-verify against HF original ────────────────────────────────────
if [[ -n "$HF_URL" ]]; then
  log "verifying composition match against HF URL"
  SCRIPT_DIR="$(dirname "$0")"
  if "$SCRIPT_DIR/verify-image-match.py" \
       --user-image "$RECIPE_DIR/hero.jpg" \
       --hf-url "$HF_URL" >> "$LOG" 2>&1; then
    log "verify PASS"
  else
    rc=$?
    log "verify exit=$rc — trying ONE retry with stronger prompt"
    # one retry: same chat, additional message with retry prompt
    osascript <<'EOF' >/dev/null
tell application "System Events"
  tell process "ChatGPT"
    set inputArea to UI element 1 of scroll area 3 of group 2 of splitter group 1 of group 1 of window 1
    click inputArea
    delay 0.4
  end tell
end tell
EOF
    send_prompt "$RETRY_PROMPT"
    poll_until_done 120 || log "WARN retry polling timed out"
    # re-extract from the new latest image
    read -r CX CY <<<"$(osascript <<'EOF' 2>/dev/null
try
  tell application "System Events"
    tell process "ChatGPT"
      set mainG to group 2 of splitter group 1 of group 1 of window 1
      set lst to list 1 of UI element 1 of scroll area 1 of mainG
      set foundBtn to missing value
      repeat with grp in (groups of lst)
        repeat with btn in (buttons of grp)
          try
            set sz to size of btn
            if (item 2 of sz) > 400 then set foundBtn to btn
          end try
        end repeat
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
)"
    [[ "$CX" != "0" ]] && {
      cliclick rc:$CX,$CY
      sleep 0.5
      osascript -e 'tell application "System Events" to key code 125' >/dev/null
      sleep 0.2
      osascript -e 'tell application "System Events" to key code 36' >/dev/null
      sleep 1.2
      osascript <<APPLE >/dev/null
set imgData to the clipboard as «class PNGf»
set fd to open for access POSIX file "$RECEIVED/restyled-fullres.png" with write permission
set eof of fd to 0
write imgData to fd
close access fd
APPLE
      sips -s format jpeg -s formatOptions 92 \
        "$RECEIVED/restyled-fullres.png" \
        --out "$RECEIVED/restyled-final.jpg" >/dev/null
      cp "$RECEIVED/restyled-final.jpg" "$RECIPE_DIR/hero.jpg"
      log "retry image saved"
    }
  fi
fi

# ─── 10. notes.md ──────────────────────────────────────────────────────────
cat > "$RECEIVED/notes.md" <<EOF
# HF #$NR — $SLUG

- **Slug:** $SLUG
- **HelloFresh Original:** ${HF_URL:-N/A}
- **Restyle-Datum:** $(date '+%Y-%m-%d %H:%M')

## Files
- original.jpg — HF-Hauptbild (Source)
- restyled-fullres.png — ChatGPT image-1 Full-Resolution
- restyled-final.jpg — q92 JPEG → recipes/$SLUG/hero.jpg

## Style-Referenzen
$(ls "$STYLE_DIR"/*.jpg 2>/dev/null | sed 's|^|- |')

## Pipeline-Log
Siehe restyle.log
EOF

# ─── 11. optional notify hook (best-effort) ────────────────────────────────
# Set NOTIFY_CMD to any shell command run on completion (webhook curl, desktop
# notification, Slack post, ...). {hero} -> hero image path, {nr} -> recipe number.
if [[ "$NOTIFY" -eq 1 && -n "${NOTIFY_CMD:-}" ]]; then
  cmd=${NOTIFY_CMD//\{hero\}/$RECIPE_DIR/hero.jpg}
  cmd=${cmd//\{nr\}/$NR}
  log "running notify hook"
  bash -c "$cmd" >/dev/null 2>&1 || true
fi

# ─── done sentinel ──────────────────────────────────────────────────────────
touch "$DONE"
log "DONE — sentinel touched at $DONE"
