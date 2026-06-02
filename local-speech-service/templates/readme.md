# local-speech-service

Local-first HTTP-Service für STT (faster-whisper) und TTS (Piper).
Läuft als launchd-Daemon auf Apple Silicon.

Installiert bei: `__INSTALL_DIR__`
Port: `__PORT__`
Whisper-Modell: `__WHISPER_MODEL__`
Piper-Default-Voice: `__DEFAULT_VOICE__`

## Endpoints

| Endpoint | Methode | Beschreibung |
|---|---|---|
| `/health` | GET | Liveness-Check |
| `/info` | GET | Modell-Info |
| `/voices` | GET | Verfügbare Piper-Stimmen |
| `/transcribe` | POST multipart | Audio → Text (faster-whisper) |
| `/synthesize` | POST JSON | Text → WAV (Piper) |

## Curl-Beispiele

```bash
# Health
curl http://localhost:__PORT__/health

# Transcribe (WAV/WebM/MP3 → Text)
curl -X POST http://localhost:__PORT__/transcribe \
  -F "audio=@recording.webm" \
  -F "language=de"

# Synthesize (Text → WAV in audio.wav speichern)
curl -X POST http://localhost:__PORT__/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text":"Hallo Welt, das ist ein Test."}' \
  --output audio.wav
```

## Wartung

```bash
# Status
launchctl print gui/$(id -u)/com.local-speech-service

# Restart
launchctl bootout gui/$(id -u)/com.local-speech-service
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.local-speech-service.plist

# Logs
tail -f __INSTALL_DIR__/service.log
tail -f __INSTALL_DIR__/service.stderr.log
```

## Whisper-Modelle (Disk + RAM)

| Modell | Größe | RAM | DE-Qualität | Latenz auf M1 |
|---|---|---|---|---|
| tiny | 39 MB | 1 GB | mittel | < 0.5s / 5s audio |
| base | 74 MB | 1 GB | mittel-gut | < 0.5s / 5s audio |
| small | 244 MB | 2 GB | gut | < 1s / 5s audio |
| medium | 769 MB | 5 GB | sehr gut | ~1.5s / 5s audio |
| large-v3-turbo (recommended) | 809 MB | 6 GB | exzellent | ~1.5s / 5s audio |
| large-v3 | 1550 MB | 10 GB | exzellent | ~3s / 5s audio |

Modellwechsel: `SPEECH_WHISPER_MODEL` in der plist setzen, Service neu starten. Modelle werden beim ersten Start automatisch heruntergeladen (~/.cache/huggingface/).

## Piper-Stimmen

Voices liegen in `__VOICES_DIR__` als `.onnx` + `.onnx.json`.

DE-Stimmen die der Installer optional zieht:
- `de_DE-thorsten-medium` (Default) — männlich, klar, neutral
- `de_DE-thorsten-high` — wie thorsten-medium aber hochauflösender (22kHz)
- `de_DE-kerstin-low` — weiblich, schneller, leichter robotic
- `de_DE-ramona-low` — weiblich, warmer Ton

Neue Stimmen nachladen:
```bash
cd __VOICES_DIR__
curl -L -O https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/<voice>/<quality>/<voice>-<quality>.onnx
curl -L -O https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/<voice>/<quality>/<voice>-<quality>.onnx.json
```
