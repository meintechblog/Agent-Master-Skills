---
name: video-transcribe
description: "Transkribiert beliebige YouTube-Videos (oder lokale Audio/Video-Dateien) zu sauberem Markdown via whisper.cpp lokal auf Apple Silicon. Zieht zuerst YouTube-Auto-Captions (Sekunden) und fällt nur zu Whisper zurück wenn Captions fehlen/mies sind. Output: Markdown mit Timestamps + Frontmatter. Optimiert für Tech-Videos (DE+EN). Designed für Background-Batch-Runs."
argument-hint: "<youtube-url-oder-id-oder-audio-file> [--output <pfad.md>] [--lang auto|de|en] [--model tiny|base|small|medium|large] [--no-captions-first] [--background]"
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
---

<objective>
Transkribiert ein Video (YouTube-URL, YouTube-ID, oder lokale Audio/Video-Datei)
in saubere, durchsuchbare Markdown-Transcripts.

**Strategie (gestaffelt für Geschwindigkeit):**
1. **YouTube-Auto-Captions** (Sekunden, gratis): yt-dlp pullt offizielle/auto-generated Captions
2. **whisper.cpp medium-Quantized** (Apple Silicon Metal, ~0.05–0.1× Realtime): wenn Captions fehlen oder Quality-Check fail
3. **whisper.cpp large** (langsamer aber bester Quality): nur auf explizite Anfrage

**Output:**
- Markdown-File mit:
  - Frontmatter (`source_url`, `video_id`, `duration`, `language`, `model`, `transcribed_at`)
  - Timestamps alle 30 s als `[mm:ss]`-Marker
  - Heading-Path-Splitting bei langen Pausen (>3s) als Absatz
- Optional SRT-Datei für Subtitle-Integration

**Vorbedingungen (one-time Setup pro Mac):**
- `brew install ffmpeg whisper-cpp yt-dlp` (alle Apple-Silicon-optimiert)
- Whisper-Model `~/.cache/whisper-cpp/ggml-medium.bin` (~530 MB) — automatisch gepullt beim ersten Lauf

</objective>

<usage>

```bash
# Einfachster Fall: YouTube-URL transkribieren
~/.claude/skills/video-transcribe/scripts/transcribe.sh "https://youtu.be/8li4By5I3p0"
# → schreibt nach /tmp/transcripts/8li4By5I3p0.md

# Mit explizitem Output-Pfad
~/.claude/skills/video-transcribe/scripts/transcribe.sh "8li4By5I3p0" \
  --output ~/notes/video-transcripts/my-video.md

# Forciere DE-Erkennung (auto-detect ist meist gut, aber bei kurzen Videos hilfreich)
~/.claude/skills/video-transcribe/scripts/transcribe.sh "URL" --lang de

# Skip YouTube-Captions, immer Whisper
~/.claude/skills/video-transcribe/scripts/transcribe.sh "URL" --no-captions-first

# Batch-Modus: alle URLs aus einer Datei
~/.claude/skills/video-transcribe/scripts/batch-transcribe.sh ~/video-urls.txt /output/dir/

# Background-Modus: returnt sofort, fertige Markdown landet im Output-Pfad
~/.claude/skills/video-transcribe/scripts/transcribe.sh "URL" --background
```

</usage>

<workflow>

1. **Input parsen** — YouTube-URL/ID erkennen, oder lokales File
2. **YouTube-Captions versuchen** (wenn Quelle YouTube + nicht --no-captions-first):
   - `yt-dlp --skip-download --write-auto-sub --sub-lang de,en --sub-format vtt <url>`
   - VTT-File parsen → Markdown
   - Quality-Check: > 50 % nicht-leere Zeilen, Length > 60 s an gesprochener Zeit → akzeptieren
   - Wenn Quality-fail → Fallback auf Whisper
3. **Whisper-Fallback** (wenn nötig):
   - `yt-dlp -x --audio-format wav --audio-quality 0 <url>` für Audio-Extraktion
   - `whisper-cli -m ~/.cache/whisper-cpp/ggml-medium.bin -t 8 -p 1 -f <wav> -ovtt -of <output-base>`
   - VTT → Markdown
4. **Markdown-Format**:
   - Frontmatter mit Metadaten
   - Timestamps alle ~30 s
   - Absätze bei längeren Pausen
5. **Cleanup** — temp wav/vtt löschen

</workflow>

<quality-heuristics>

**YouTube-Captions sind gut wenn:**
- Auto-generated Captions verfügbar (für Top-2000-Channels üblich)
- DE oder EN content (für andere Sprachen sind Captions oft mies)
- > 5 Min Video (Captions decken den ganzen Track ab)

**Whisper braucht man wenn:**
- Captions disabled vom Uploader
- Video ist < 5 min (Captions oft unvollständig)
- Sehr technischer Content (Auto-Captions verstehen "Hub4Mode" nicht, Whisper schon)
- Hintergrundgeräusche / Akzent (Whisper robuster)

</quality-heuristics>

<output-spec>

Markdown-Output-Format:

```markdown
---
title: "Getting Started with Rust — Ownership Explained"
source_url: https://youtu.be/VIDEO_ID
video_id: VIDEO_ID
channel: example-channel
duration_s: 1247
language: de
model: whisper-medium (or "youtube-auto-captions")
transcribed_at: 2026-05-28
tags: [rust, programming, tutorial]
---

# Getting Started with Rust — Ownership Explained

> Quelle: [Example Channel via YouTube](https://youtu.be/VIDEO_ID) — transkribiert 2026-05-28

## Transcript

[00:00] Hi everyone, today we're looking at ownership and borrowing
in Rust and why the compiler is your friend...

[00:30] Let's start with a simple example where...
```

</output-spec>

<known-limits>

- **Diarization** (Speaker-Separation) ist via `--diarize` Flag möglich aber für mono YT-Videos meist sinnlos
- **DE-Technical-Terms**: "Volkswagen" vs "Folksvagen" — Whisper macht da manchmal Fehler. Bei Bedarf via `--prompt` ein Tech-Lexikon mitgeben (TODO: extend script)
- **Mac M1/M2/M3/M4** sind alle gut. Auf älteren Intel-Macs ist whisper.cpp ~5x langsamer aber funktional.
- **Speicher**: medium-Model braucht ~2 GB RAM beim Inferenz. Large braucht ~3-4 GB.
- **Sprache-Auto-Detect** funktioniert ab ~30 s Audio. Bei sehr kurzen Videos: explizit `--lang` setzen.

</known-limits>

<integration-with-knowledge-base>

Für den Ingest in eine eigene Wissensdatenbank/Webapp (Beispiel):

```bash
# 1. Transcribe ein Video direkt in den own-findings-Pfad
~/.claude/skills/video-transcribe/scripts/transcribe.sh \
  "https://youtu.be/VIDEO_ID" \
  --output ~/your-kb-repo/docs/video-transcripts/my-video.md

# 2. Sync + Ingest
cd ~/your-kb-repo && bash scripts/sync-content.sh

# 3. Via vk querybar
scripts/kb-search "rust ownership borrowing"
```

</integration-with-knowledge-base>
