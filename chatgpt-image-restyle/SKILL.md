---
name: chatgpt-image-restyle
description: "Beliebiges Quellbild auf einen konsistenten visuellen Stil restylen via ChatGPT.app — Few-Shot mit eigenen Style-Referenzbildern, paralleler Background-Mode, Auto-Verify-Loop mit Retry. Macht aus einem HelloFresh-Karten-Foto oder einer Web-Image-URL ein neues Bild im gewünschten App-/Brand-Stil."
argument-hint: "<pfad/zum/quell-bild> --style-refs <pfad/zum/style-folder> --output <pfad/zur/jpeg-ausgabe> [--diet vegan|vegetarian] [--preserve \"...\"] [--background] [--verify-url <url>]"
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - WebFetch
---


<objective>
Take a source image (HelloFresh card photo, web hero image, anything) and
produce a restyled version in a consistent visual style — defined by 1–N
style-reference images you provide as "few-shot anchors". Designed to be
called from other skills (e.g. a recipe publisher, image-pipeline-of-X) or
ad-hoc from a session.

**Output:**
- final JPEG (q92 by default) at `--output`
- optional full-resolution PNG at `--output-png`
- optional sentinel file `.done` in `--background` mode

**Vorbedingungen (Setup einmal pro Mac):**
- macOS mit ChatGPT.app installiert + eingeloggt
- Bedienungshilfen-Permission für osascript-UI-Scripting erlaubt (System Settings → Privacy → Accessibility → das verwendete Terminal/Iterm)
- `cliclick` installiert: `brew install cliclick`
- 1–N Style-Referenzbilder in einem Folder (alphabetisch sortiert beim glob)

**Output ist deterministisch genug**: gleiche Style-Refs + gleicher Prompt → konsistenter Look. Variation in der Anordnung der Hauptzutaten ist explizit gewünscht (kein 1:1-Copy).
</objective>


<execution_context>
SKILL_DIR=${SKILL_DIR:-$HOME/.claude/skills/chatgpt-image-restyle}
</execution_context>


<usage>

## Schnellstart

```bash
$SKILL_DIR/scripts/restyle.sh \
  --target /pfad/zum/quell-bild.jpg \
  --style-refs /pfad/zum/folder/mit/3-style-jpgs \
  --output /pfad/zum/ergebnis.jpg
```

Das macht alles automatisch: ChatGPT.app neuer Chat, 3 Style-Refs pasten, Target pasten, default Prompt senden, Polling auf Done, Right-Click → "Bild kopieren", Clipboard-PNG → JPEG q92 → `--output`.

## Voller Argument-Satz

| Argument | Pflicht? | Default | Bedeutung |
|---|---|---|---|
| `--target <pfad>` | ja | — | Quellbild das restyled werden soll |
| `--style-refs <dir>` | ja | — | Folder mit Style-Anker-Bildern (`*.jpg`/`*.png`, alphabetisch sortiert) |
| `--output <pfad.jpg>` | ja | — | Ziel-JPEG-Pfad |
| `--output-png <pfad.png>` | nein | — | Falls gesetzt: auch das Full-Res-PNG hier speichern |
| `--background` | nein | off | Skript returnt sofort, läuft im Hintergrund via `nohup`, schreibt `.done` neben `--output` wenn fertig |
| `--log <pfad>` | nein | stderr | Wohin der timestamp-prefixed Log soll |
| **Prompt-Tuning** ||||
| `--source-mode <photo\|recipe-card\|menu-shot>` | nein | `photo` | `recipe-card` aktiviert Card-Mode-Prompt (Target ist Foto einer gedruckten Karte) |
| `--diet <vegan\|vegetarian>` | nein | — | Ergänzt Prompt um Anti-Fleisch-Disclaimer (gegen "Stroganoff-Bias") |
| `--main-subjects "<liste>"` | nein | — | Hauptbestandteile namentlich auflisten (z.B. "Portobello-Pilze, Fusilli, Kürbiskerne") |
| `--preserve "<liste>"` | nein | — | Garnitur-/Akzent-Anker die NICHT weggelassen werden dürfen (z.B. "Zitronenkeile, Frühlingszwiebel") |
| `--prompt <text>` | nein | auto | Komplette Override des default-Prompts |
| `--retry-prompt <text>` | nein | auto | Komplette Override des Retry-Prompts |
| **Verify-Loop** ||||
| `--verify-url <url>` | nein | — | Nach Generierung wird das Result gegen diese URL gematched (via verify-image-match.py falls verfügbar). Bei Mismatch: 1× Retry mit verschärftem Prompt |
| `--max-retries <N>` | nein | 1 | Wie viele Retries bei verify-Mismatch |
| **Notification** ||||
| `--notify "<cmd>"` | nein | — | Shell-Cmd das nach Erfolg ausgeführt wird. Platzhalter: `{output}`, `{output_png}`, `{target}`. Für Notifications (Webhook / Slack / eigenes Skript) |

## Beispiele

**Cookidoo-Rezept-Restyle** (wird z.B. von einem Rezept-Publisher-Skill so aufgerufen):

```bash
$SKILL_DIR/scripts/restyle.sh \
  --target $REPO/.received/hf32/original.jpg \
  --style-refs $REPO/style-references \
  --output $REPO/recipes/vegane-filetstuecke-thai-orange/hero.jpg \
  --output-png $REPO/.received/hf32/restyled-fullres.png \
  --diet vegan \
  --main-subjects "Tofu-Würfel in Orangensoße, Jasminreis, Cashews, Frühlingszwiebel" \
  --preserve "Zitronenspalten, Sesam-Topping" \
  --verify-url "https://www.hellofresh.de/recipes/vegane-filetstuecke-..." \
  --notify "$REPO/scripts/notify.sh restyle-done hf32 {output}" \
  --background
```

**Brand-Logo-Restyle** (ein-Style-Anker reicht):

```bash
$SKILL_DIR/scripts/restyle.sh \
  --target ~/Downloads/sketch.png \
  --style-refs ~/branding/anchors \
  --output ~/branding/logo-restyled.jpg
```

**Foto-einer-Visitenkarte → App-Avatar**:

```bash
$SKILL_DIR/scripts/restyle.sh \
  --target ~/photos/IMG_1234.jpg \
  --style-refs ~/avatar-style-pack \
  --output ~/avatars/john.jpg \
  --source-mode menu-shot \
  --preserve "Logo-Position oben links"
```

## Was hinter den Kulissen passiert

1. **Voraussetzungs-Check** — `cliclick` + ChatGPT.app + Bedienungshilfen
2. **Neuer ChatGPT-Chat** via Toolbar-Button (`button 2 of toolbar 1`)
3. **Input-AXTextArea fokussieren** via typed AppleScript reference (`scroll area 3 of group 2 of splitter group 1 of group 1 of window 1`)
4. **Style-Referenzen pasten** — Loop über `--style-refs/*.{jpg,png}`, jede via `osascript … set the clipboard to (read POSIX file … as JPEG picture)` + Cmd+V + 2s
5. **Target pasten** als letztes Bild
6. **Prompt zusammenbauen** aus default + diet-hint + main-subjects + preserve + source-mode (siehe `references/prompt-recipes.md`)
7. **Senden** (Return)
8. **Polling** alle 3s — checked AX-Tree auf "Bild wird erstellt" weg + image-Button > 400px da, max 120s
9. **Right-Click auf Image-Center** (Position aus AX-Tree) → Down → Return → "Bild kopieren"
10. **Clipboard-PNG → File** (`osascript … the clipboard as «class PNGf»`)
11. **JPEG q92 via sips** + Kopieren ins `--output`
12. **Optional Auto-Verify** mit `--verify-url`: ruft verify-image-match.py auf, bei Mismatch 1× Retry mit verschärftem Prompt im selben Chat
13. **Optional Notify** mit `--notify` Hook
14. **Sentinel** `.done` setzen (nur im `--background` Mode)

## Failure-Modes & Debugging

Siehe `references/failure-modes.md` für eine kompakte Sammlung (Polling-Timeout, "Bild kopieren" nicht erstes Item, falscher Chat aktiv, etc.).

UI-Tree-Pfade in ChatGPT.app: `references/applescript-paths.md`.

Prompt-Templates und Bias-Mitigation: `references/prompt-recipes.md`.

## Wer ruft das auf?

- ein Rezept-Publisher-Skill (Hero-Bild-Phase) — für AI-Hero-Bilder
- Ad-hoc aus jeder Claude-Code-Session, wenn Mac + ChatGPT.app verfügbar
- Andere Skills die einen Bild-Restyle-Schritt brauchen — call as subprocess

## Wer ruft das NICHT auf?

- LXC/Server-only-Sessions ohne Zugriff auf Mac-GUI (keine ChatGPT.app)
- Sessions ohne Bedienungshilfen-Permission
- Headless-Cloud-Skills — die müssen die OpenAI API direkt nutzen (kein UI-Scripting möglich)
</usage>
