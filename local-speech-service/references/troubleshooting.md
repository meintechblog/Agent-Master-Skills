# local-speech-service Troubleshooting

## Service startet, aber /health antwortet nicht

```bash
tail -f ~/.local-speech-service/service.stderr.log
```

Häufige Ursachen:
- `faster-whisper` Modell-Download läuft noch (>5min für large-v3 möglich, je nach Internet)
- Python-Import-Fehler → check stderr, vermutlich pip-Dep fehlt → re-run install.sh
- Port 8765 schon belegt → `lsof -i :8765`, install.sh mit `--port 8766` neu

## Piper-Binary "nicht gefunden"

Apple-Silicon-Releases hatten in der Vergangenheit unterschiedliche tar-Layouts. Check:
```bash
find ~/.local-speech-service -name piper -type f
```

Wenn `piper` da ist aber Service findet's nicht: `SPEECH_PIPER_BIN` in der plist anpassen, dann Service restart.

## Voice "not found"

```bash
curl http://localhost:8765/voices
```

Wenn deine Voice nicht in der Liste ist, manuell ziehen:
```bash
cd ~/.local-speech-service/voices/
# Pattern: de_DE-thorsten-medium → locale=de_DE, name=thorsten, quality=medium
LOCALE=de_DE; NAME=thorsten; QUALITY=medium; LANG=de
URL=https://huggingface.co/rhasspy/piper-voices/resolve/main/$LANG/$LOCALE/$NAME/$QUALITY
curl -L -O "$URL/${LOCALE}-${NAME}-${QUALITY}.onnx"
curl -L -O "$URL/${LOCALE}-${NAME}-${QUALITY}.onnx.json"
```

## Whisper läuft, aber Transkripte sind grottig

Ursachen:
- Modell zu klein für deine Audio-Quality → upgrade auf `large-v3-turbo`
- Audio hat Hintergrund-Lärm → VAD (Voice-Activity-Detection) filtert das schon, hilft aber nur begrenzt
- Language-Detect liegt falsch → expliziten `language` Form-Param schicken: `-F "language=de"`
- Audio-Format wird nicht unterstützt → faster-whisper braucht ffmpeg im PATH:
  ```bash
  brew install ffmpeg
  ```

## TTS klingt robotic

- Piper `low`-Variants klingen robotic by design. Auf `medium` upgraden:
  ```bash
  ~/.claude/skills/local-speech-service/scripts/install.sh \
    --default-voice de_DE-thorsten-medium --force
  ```
- Bei sehr emotionalem Text bleibt Piper plateau-Stimme — das ist eine Engine-Limitation
- Für noch bessere Quality: Kokoro-82M oder F5-TTS (deutlich aufwendiger zu installieren, derzeit nicht in diesem Skill)

## Service crashed beim Modell-Laden

Wahrscheinlich Out-Of-Memory:
- large-v3 = 10 GB RAM-Spike beim Initial-Load
- large-v3-turbo = 6 GB
- medium = 5 GB
- small = 2 GB

Andere Apps zumachen oder kleineres Modell.

## CORS-Errors im Browser

Default `--cors-origins "*"` lässt alles durch. Wenn das nicht klappt:
- Service wurde mit `--cors-origins http://something` installiert und Browser ist auf anderer Origin
- Browser blockiert wegen mixed-content (http vs https)
- Service ist nur auf 127.0.0.1 gebunden aber Browser kommt aus LAN

Fix:
```bash
~/.claude/skills/local-speech-service/scripts/install.sh \
  --cors-origins "*" --host 0.0.0.0 --force
```

## Service neu starten

```bash
launchctl bootout gui/$(id -u)/com.local-speech-service
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.local-speech-service.plist
```

Oder kurz: `kickstart -k`:
```bash
launchctl kickstart -k gui/$(id -u)/com.local-speech-service
```

## Komplett deinstallieren

```bash
launchctl bootout gui/$(id -u)/com.local-speech-service
rm ~/Library/LaunchAgents/com.local-speech-service.plist
rm -rf ~/.local-speech-service
rm -rf ~/.cache/huggingface/hub/models--Systran--faster-whisper*
```
