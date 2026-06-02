# Failure-Modes & Diagnose

Häufige Fehlerquellen beim chatgpt-image-restyle, gefunden aus Live-Runs.

## Pre-flight

| Symptom | Ursache | Fix |
|---|---|---|
| `ERROR cliclick missing` | Tool fehlt | `brew install cliclick` |
| `ERROR ChatGPT.app not installed/running` | App fehlt oder Permission | App im Dock starten; in System Settings → Privacy → Accessibility das Terminal whitelisten |
| `osascript` Permission-Denied | Bedienungshilfen-Permission fehlt | gleiche Stelle, ggf. mit `tccutil reset Accessibility com.apple.Terminal` neu setzen |
| `ERROR no style refs in <dir>` | Folder leer oder falscher Pfad | `ls $STYLE_REFS_DIR/*.jpg` checken |

## Während der Pipeline

### Polling-Timeout (>120s)

**Symptom:** Log endet bei `polling for completion (max 120s)` + danach `WARN polling timed out — proceeding anyway`.

**Ursachen + Fixes:**
- ChatGPT-Auto routet nicht zum Image-Model. Im neuen Chat statt aus bestehendem mit Voreinstellungen senden. Im Toolbar das Model auf „image-1" / „GPT-4o" explicit setzen.
- Send-Aktion ist nicht durchgegangen weil Input nicht fokussiert war. Probe: in der Live-Session screenshot machen, schauen ob Prompt-Text + Bilder im Input-Feld sichtbar sind.
- Send-Animation hat das Bild als „Generating" markiert aber kein Image-Button kommt — manchmal bei Model-Rate-Limits. Versuch nochmal in 1-2 Min.

### „could not locate image button"

**Symptom:** Polling sagt done, aber AX-Locator findet kein image-Button.

**Ursache:** ChatGPT.app UI-Drift — Pfad zum AXButton hat sich geändert.

**Diagnose:**
```bash
osascript <<'EOF' > /tmp/cg-tree.txt
tell application "System Events"
  tell process "ChatGPT"
    repeat with idx from 1 to (count of UI elements of window 1)
      set el to item idx of (UI elements of window 1)
      log (idx as text) & ": " & (role of el) & " " & (description of el)
    end repeat
  end tell
end tell
EOF
cat /tmp/cg-tree.txt
```

Dann `references/applescript-paths.md` updaten und `locate_latest_image` in `scripts/restyle.sh` anpassen.

### „no PNG data in clipboard"

**Symptom:** Right-Click ist passiert, aber Clipboard hat keine `«class PNGf»` Daten.

**Ursachen:**
- Menü-Reihenfolge geändert: "Bild kopieren" ist nicht mehr 1. Item. Statt `key code 125 (Down) + key code 36 (Return)` mehrere `Down`-Presses einfügen, oder UI-Tree-Dump:
  ```applescript
  menu items of menu 1   -- listet alle Items auf
  ```
- Cliclick hat den falschen Spot getroffen (z.B. außerhalb des Bildes). Bild-Center neu berechnen.
- Menu zu schnell wieder zu — `sleep 0.5` zwischen rc und Down erhöhen.

### Falscher Chat aktiv beim Pasten

**Symptom:** Pipeline startet, aber die Style-Refs landen in einem alten Chat (sichtbar als „Zusammenarbeit mit Terminal Tab"-Chip o.ä.).

**Ursache:** Wir haben aus einem bestehenden Chat heraus gepastet, nicht aus einem neuen.

**Fix:** Immer mit `click button 2 of toolbar 1` einen frischen Chat öffnen (das macht der Skill schon).

### Wenn das Restyle 1× falsch interpretiert wird

**Symptom:** Result-Bild zeigt was anderes als gewünscht (z.B. Hähnchen statt Pilze bei "Veganem Stroganoff").

**Fix:** `--diet vegan` + `--main-subjects "<liste>"` setzen — das hängt explizite Anti-Fleisch-Hints + Hauptbestandteile-Liste an. Siehe `references/prompt-recipes.md` § "Bias-Mitigation".

Beim Retry: das Skript hängt automatisch `--retry-prompt` an, der die gleichen Diet/Main/Preserve-Hints behält. Bei `--max-retries 2` kannst du noch eine 2. Runde machen.

### Garnituren gehen beim Retry verloren

**Symptom:** 1. Version hat Zitronenkeile, 2. Version nicht mehr.

**Fix:** `--preserve "Zitronenkeile, Petersilie, ..."` setzen — die werden in beide Prompts (default UND retry) eingebaut.

## Verify-Score zu niedrig

**Symptom:** `verify-image-match.py` exit 1 (Score < 25%) obwohl das Bild OK aussieht.

**Mögliche Ursachen:**
- Verify-Script erwartet HF-Foto-Look (weiß, klinisch). AI-Restyle hat dunkler/wärmer Look — Composite-Score fällt.
- HF-URL hat sich geändert (alter Cache, anderes Hero-Bild).

**Fix:** Manueller Vergleich. `--max-retries 0` setzen wenn dem Verify-Script bei AI-Bildern nicht zu trauen ist.

## Background-Mode hängt

**Symptom:** `--background` returnt sofort, aber `.done`-Sentinel kommt nie.

**Diagnose:**
- Log lesen: `tail -f ${OUTPUT%.*}.restyle.log`
- PID checken: `cat ${OUTPUT}.pid` → `ps -p <pid>`
- Wenn Prozess tot aber kein `.done` da: irgendwo crashed. Letzte Log-Zeile zeigt wo.

**Bekannte Crashes:**
- `cp src dst` mit src == dst → exit 1 unter macOS (selbst-copy auf gleichen Pfad). Skill skipped das jetzt automatisch.
- `set -e` in Subshells bei sed/python pipefail.
