---
name: local-speech-service
description: "Installiert einen lokalen STT+TTS HTTP-Service auf Apple Silicon. faster-whisper (large-v3-turbo) für Speech-to-Text, Piper-TTS (neural, mehrere DE-Stimmen) für Text-to-Speech. launchd-managed, LAN-erreichbar, 0 Cloud-Calls, 0 API-Keys. Wird von webapp-chat-bridge (und anderen Skills) als Voice-Backend genutzt."
argument-hint: "[--whisper-model large-v3-turbo|small|medium] [--default-voice de_DE-thorsten-medium] [--port 8765] [--service-token <val>]"
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
---

<objective>
Local-first speech I/O als HTTP-Service auf Apple Silicon. Wird als reusable
Dependency von anderen Skills (webapp-chat-bridge mit `--enable-voice`,
custom voice-assistants, etc.) eingebunden.

**Was läuft nach Install:**
- launchd-Daemon `com.local-speech-service` auf Port 8765
- `POST /transcribe` — multipart audio (WebM/WAV/MP3) → JSON `{"text":"..."}`
- `POST /synthesize` — JSON `{"text":"..."}` → audio/wav stream
- `GET /health`, `/info`, `/voices` — Monitoring + Voice-Picker

**Stack-Wahl (state-of-the-art 2026 für Apple Silicon):**
- **STT: faster-whisper `large-v3-turbo`** — Apple Metal accelerated CT2-Backend.
  RTF ~0.2 auf M-Chip → 5s Audio in <1s. Exzellente DE-Quality.
- **TTS: Piper TTS (neural)** — open-source ONNX-Modelle, CPU-only OK.
  `thorsten-medium` als DE-Default ist sehr natürlich.

**Komplett lokal**: keine Cloud-Hops, keine API-Keys, keine OpenAI/ElevenLabs/Google
beteiligt. Audio-Streams verlassen das LAN nicht.
</objective>

<execution_context>
SKILL_DIR=${SKILL_DIR:-$HOME/.claude/skills/local-speech-service}
</execution_context>

<usage>

## Schnellstart

```bash
~/.claude/skills/local-speech-service/scripts/install.sh
```

Default-Install macht:
1. Python venv unter `~/.local-speech-service/venv` + faster-whisper, fastapi, uvicorn
2. Piper-Binary aus GitHub-Releases (für Apple Silicon `aarch64`)
3. Download de_DE-thorsten-medium voice (~60MB) von HuggingFace
4. Preload Whisper large-v3-turbo (~800MB) — beim ersten Start sonst.
5. launchd-Job bootstrapped → läuft sofort + bei Reboot

Nach ~5-15min (je nach Internet) ist Port 8765 ready:
```bash
curl http://localhost:8765/health
# → {"status":"ok",...}
```

## Argument-Tabelle

| Flag | Default | Bedeutung |
|---|---|---|
| `--install-dir <path>` | `~/.local-speech-service` | Wo venv + Modelle + Voices liegen |
| `--whisper-model <name>` | `large-v3-turbo` | `tiny\|base\|small\|medium\|large-v3-turbo\|large-v3` |
| `--whisper-lang <code>` | `de` | Default-Language-Hint (auto-detect läuft trotzdem) |
| `--default-voice <name>` | `de_DE-thorsten-medium` | Piper Voice für `/synthesize` ohne explizit voice-Param |
| `--extra-voices <csv>` | (none) | Weitere DE-Voices runterziehen, z.B. `de_DE-kerstin-low,de_DE-ramona-low` |
| `--port <n>` | `8765` | HTTP-Port |
| `--host <ip>` | `0.0.0.0` | Bind-Address. `127.0.0.1` = localhost-only |
| `--cors-origins <csv>` | `*` | Browser-CORS-Whitelist. Bei Production: konkret setzen |
| `--service-token <val>` | (none) | Bearer-Token wenn ihr's exposeren wollt |
| `--skip-launchd` | off | Plist schreiben aber nicht starten |
| `--skip-piper` | off | Piper-Install überspringen (TTS später) |
| `--skip-whisper-preload` | off | Modell beim ersten Request lazy-downloaden |
| `--force` | off | Bestehenden Install überschreiben |

## Modell-Wahl: welches Whisper?

Apple Silicon Benchmarks (M1, 16GB):

| Modell | Größe | DE-Quality | 10s Audio | Empfehlung |
|---|---|---|---|---|
| tiny | 39 MB | OK | 0.4s | Smartwatch, embedded |
| base | 74 MB | gut | 0.4s | low-resource |
| small | 244 MB | sehr gut | 0.8s | Sweet-Spot bei kleinen Macs |
| medium | 769 MB | sehr gut | 1.5s | OK Power-Usage |
| **large-v3-turbo** ⭐ | 809 MB | exzellent | 1.5s | **Default — gleiche Quality wie large-v3, halbe Latenz** |
| large-v3 | 1.5 GB | exzellent | 3.0s | Wenn max-Quality > Latenz |

## Voice-Wahl

Default `de_DE-thorsten-medium` ist gut. Alternative DE-Voices via `--extra-voices`:

| Voice | Geschlecht | Style | Bewertung |
|---|---|---|---|
| `de_DE-thorsten-medium` ⭐ | männlich | neutral, klar | Default, sehr natürlich |
| `de_DE-thorsten-high` | männlich | wie medium, 22kHz | Higher fidelity |
| `de_DE-kerstin-low` | weiblich | schneller, leicht robotic | Schnell aber etwas synthetic |
| `de_DE-ramona-low` | weiblich | warm | Alternative weibliche Stimme |
| `de_DE-eva_k-x_low` | weiblich | kindlich, leichter Akzent | nicht für Standard-Use |
| `de_DE-pavoque-low` | männlich | älter, etwas blechern | nicht empfohlen |

Alle Voices verfügbar bei: https://huggingface.co/rhasspy/piper-voices/tree/main/de/de_DE

## Beispiele

**Default Install (large-v3-turbo + thorsten-medium):**
```bash
~/.claude/skills/local-speech-service/scripts/install.sh
```

**Lightweight Mac (small + nur eine Voice):**
```bash
~/.claude/skills/local-speech-service/scripts/install.sh \
  --whisper-model small
```

**Multi-Voice + Bearer-Auth:**
```bash
~/.claude/skills/local-speech-service/scripts/install.sh \
  --extra-voices "de_DE-kerstin-low,de_DE-ramona-low" \
  --service-token "$(openssl rand -hex 16)" \
  --cors-origins "http://192.0.2.10,http://localhost:3000"
```

**Localhost-only (kein LAN-Access):**
```bash
~/.claude/skills/local-speech-service/scripts/install.sh \
  --host 127.0.0.1
```

## Curl-Test

```bash
# Health
curl http://localhost:8765/health

# Info
curl http://localhost:8765/info | jq

# Voices
curl http://localhost:8765/voices | jq

# Transcribe an audio file (multipart upload)
curl -X POST http://localhost:8765/transcribe \
  -F "audio=@/path/to/sample.webm" \
  -F "language=de" | jq

# Synthesize text → WAV file
curl -X POST http://localhost:8765/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text":"Hallo Welt, das ist ein Test."}' \
  --output test.wav && afplay test.wav
```

## Wer ruft das auf?

- **webapp-chat-bridge** mit `--enable-voice` → STT für Mic-Input, TTS für Audio-Reply (Walkie-Talkie-Mode)
- Voice-Assistants jeder Art
- Browser-clients direkt via `fetch()` mit `MediaRecorder`-API

## Wer ruft das NICHT auf?

- Sessions ohne Apple Silicon Mac (LXC, Linux, Windows — andere Whisper-Pfade nötig)
- Hochfrequente Use-Cases (>10 req/s) — Whisper-Init ist single-process, würde queuen
- Audio >5 min — würde RAM hochjagen und ist nicht der Use-Case (für lange Audios → `video-transcribe`-Skill)

## Troubleshooting

Siehe `references/troubleshooting.md`.
</usage>
