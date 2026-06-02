# Hero-Bild-Pipeline

Drei Pfade je nach Verfügbarkeit. Persistent archivieren in `$SKILL_REPO/.received/hf<NR>/`.

## Pfad A — User hat eigenes Foto (BEST)

```bash
SLUG=<slug>
NR=<hf_card_number>
mkdir -p $SKILL_REPO/.received/hf$NR
cp <user-photo-path>           $SKILL_REPO/.received/hf$NR/original.jpg
cp <user-photo-path>           $SKILL_REPO/recipes/$SLUG/hero.jpg
$SKILL_DIR/scripts/verify-image-match.py \
  --user-image $SKILL_REPO/recipes/$SLUG/hero.jpg \
  --hf-url     "<hellofresh-url>"
```

→ Foto-Zeile im Recipe-README: `© <Fotograf> (eigene Aufnahme)`.

## Pfad B — AI-Restyle (HF-Original vorhanden, eigenes Foto fehlt)

Seit 2026-05-27 wrappt der generische Skill `chatgpt-image-restyle` die UI-Steuerung. cookidoo-recipe-publisher ruft den auf (statt eines eigenen Skripts):

```bash
# 1. HF-Hauptbild von HelloFresh ziehen (URL aus Phase 1)
mkdir -p $SKILL_REPO/.received/hf$NR
curl -sL "$IMAGE_URL" -o $SKILL_REPO/.received/hf$NR/original.jpg

# 2. Restyle dispatchen (Background — kehrt sofort zurück)
~/.claude/skills/chatgpt-image-restyle/scripts/restyle.sh \
  --target     $SKILL_REPO/.received/hf$NR/original.jpg \
  --style-refs $SKILL_REPO/style-references \
  --output     $SKILL_REPO/recipes/$SLUG/hero.jpg \
  --output-png $SKILL_REPO/.received/hf$NR/restyled-fullres.png \
  --log        $SKILL_REPO/.received/hf$NR/restyle.log \
  --verify-url "$HF_URL" \
  --diet       "$DIET" \
  --main-subjects "$MAIN" \
  --preserve   "$GARNISH" \
  --background
```

Volle Doku des chatgpt-image-restyle Skills: `~/.claude/skills/chatgpt-image-restyle/SKILL.md`.

Was das Script macht (Auto-Pipeline):

1. **Voraussetzungen prüfen** — `cliclick` installiert, ChatGPT.app vorhanden, Bedienungshilfen-Permission
2. **Neuer Chat** in ChatGPT.app via toolbar button („Neuer Chat")
3. **Input fokussieren** via typed AppleScript reference (`scroll area 3 of group 2 of splitter group 1 of group 1 of window 1`)
4. **3 Style-Referenzen** aus `$SKILL_REPO/style-references/*.jpg` (alphabetisch sortiert) pasten — kuratierte deterministische Auswahl, nicht zufällig
5. **HF-Target** als 4. Bild pasten
6. **Prompt** + Send (Return)
7. **Polling** auf Fertigstellung — alle 3s prüfen ob „Bild wird erstellt" weg ist UND ein image-Button > 400px Höhe da ist. Max 120s.
8. **Right-Click** auf Image-Center (Position aus AX-Tree gelesen) → Down-Arrow → Return → „Bild kopieren"
9. **Clipboard-PNG** extrahieren via `osascript ... the clipboard as «class PNGf»`
10. **JPEG q92** via `sips -s format jpeg`
11. **Auto-Verify** mit `verify-image-match.py` gegen HF-URL. Bei niedrigem Score: 1× Retry mit verschärftem „Komposition exakt halten"-Prompt im selben Chat.
12. **notes.md** mit Pipeline-Log und Style-Reference-Liste schreiben
13. **Notify** (optional, best-effort) — wenn ein Notify-Hook (`NOTIFY_CMD`) gesetzt ist, wird „Restyle für HF #N fertig — schau optisch drüber" ausgelöst (Webhook/Slack/Desktop, frei konfigurierbar)
14. **Sentinel** `.received/hf<NR>/.done` setzen → Skill-Hauptpipeline poll'd darauf

Modi:
- `--background` (empfohlen): Script disowned via nohup, returnt sofort. Skill kann parallel Steps 3-5 (tips/times/chips) laufen lassen.
- ohne `--background`: blockiert bis fertig.

Foto-Zeile im Recipe-README:
```
AI-Vorab-Bild (eigene Generierung mit ChatGPT image-1, Style-Referenzen aus eigener Rezeptesammlung, Copyright <Fotograf>) — wird beim ersten Kochen durch eigenes Plattenfoto ersetzt
```

→ Publish (06) mit `--yes` ist OK weil AI-Generierung als „eigene" gilt (commercial usage rights).

## Pfad C — Kein Foto verfügbar

Rezept als PRIVATE auf Cookidoo lassen (kein `06_publish.py --yes`-Aufruf). Foto-Zeile: `kein Foto — Rezept ist privat`. Pipeline-Stop nach Phase 6 Step 5.

## Style-Reference-Set verwalten

Aktuell im Repo: `style-references/01-sweet-chili-bowl.jpg`, `02-nasi-goreng.jpg`, `03-ingwer-curry.jpg` — siehe `style-references/README.md`. Wenn der Stil-Anker driften soll: andere 3 Bilder dort reinkopieren mit denselben Dateinamen-Konventionen (alphabetisch sortiert beim Glob).

## Failure-Modes & Diagnose

`.received/hf<NR>/restyle.log` enthält timestamped Pipeline-Output. Häufige Probleme:

- **„cliclick missing"** → `brew install cliclick`
- **„ChatGPT.app not installed/running"** → ChatGPT.app öffnen, einloggen
- **Polling-Timeout** → ChatGPT-Auto routet das Bild ggf. an einen langsameren Pfad. Im Log nachschauen ob „generating" oder „waiting" → bei „waiting" hat die Send-Aktion nicht funktioniert (Input nicht fokussiert).
- **„could not locate image button"** → ChatGPT-UI hat sich geändert. UI-Tree-Dump (siehe FAQ unten) → Pfad zum Image-AXButton im Script anpassen.
- **„no image in clipboard"** → Right-Click-Menu hatte „Bild kopieren" nicht als 1. Item. Manuell prüfen welches Item welchen Index hat.
- **Verify-Loop Retry hängt** → Script macht max 1 Retry. Bei wiederholtem Fehlschlag: Sentinel wird trotzdem gesetzt mit dem letzten Bild — User-Sicht entscheidet.

UI-Tree-Dump für Debugging:
```bash
osascript <<'EOF' >/tmp/cg-tree.txt
tell application "System Events"
  tell process "ChatGPT"
    log "window count: " & (count of windows)
    set elems to UI elements of window 1
    repeat with idx from 1 to (count of elems)
      set el to item idx of elems
      log (idx as text) & ": " & (role of el) & " " & (description of el)
    end repeat
  end tell
end tell
EOF
cat /tmp/cg-tree.txt
```
