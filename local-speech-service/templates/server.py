#!/usr/bin/env python3
"""local-speech-service — STT + TTS HTTP-Service for the LAN.

Endpoints:
  POST /transcribe   multipart audio → JSON {"text": "..."}
  POST /synthesize   JSON {"text":"..."} → audio/wav
  GET  /health       basic liveness
  GET  /voices       list available TTS voices
  GET  /info         model + version info

Stack:
  - faster-whisper (local Whisper) for STT — Apple Silicon Metal-accelerated via CT2
  - Piper TTS (neural, local) for TTS

All processing stays on this machine. No API keys.
"""
from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

# ── Config ──────────────────────────────────────────────────────────────────

WHISPER_MODEL_SIZE = os.environ.get("SPEECH_WHISPER_MODEL", "large-v3-turbo")
WHISPER_COMPUTE_TYPE = os.environ.get("SPEECH_WHISPER_COMPUTE", "int8")  # int8 = fast on Apple Silicon
WHISPER_DEVICE = os.environ.get("SPEECH_WHISPER_DEVICE", "auto")  # auto / cpu / cuda
WHISPER_LANGUAGE_DEFAULT = os.environ.get("SPEECH_WHISPER_LANG", "de")
WHISPER_BEAM_SIZE = int(os.environ.get("SPEECH_WHISPER_BEAM", "5"))

PIPER_BIN = os.environ.get("SPEECH_PIPER_BIN", "/opt/homebrew/bin/piper")
PIPER_VOICES_DIR = Path(os.environ.get(
    "SPEECH_PIPER_VOICES_DIR",
    str(Path.home() / ".local" / "share" / "piper-voices"),
))
PIPER_DEFAULT_VOICE = os.environ.get("SPEECH_PIPER_DEFAULT_VOICE", "de_DE-thorsten-medium")

SERVICE_TOKEN = os.environ.get("SPEECH_SERVICE_TOKEN", "")
PORT = int(os.environ.get("SPEECH_SERVICE_PORT", "8765"))
HOST = os.environ.get("SPEECH_SERVICE_HOST", "0.0.0.0")
CORS_ORIGINS = os.environ.get("SPEECH_CORS_ORIGINS", "*").split(",")

LOG_PATH = Path(os.environ.get(
    "SPEECH_SERVICE_LOG",
    str(Path.home() / ".local-speech-service" / "service.log"),
))

# ── Logging ─────────────────────────────────────────────────────────────────

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("speech")

# ── App ────────────────────────────────────────────────────────────────────

app = FastAPI(title="local-speech-service", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
)

# ── Lazy-load Whisper ───────────────────────────────────────────────────────

_whisper_model = None
_whisper_lock = threading.Lock()


def get_whisper():
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            log.info(f"loading whisper model: {WHISPER_MODEL_SIZE} ({WHISPER_COMPUTE_TYPE})")
            from faster_whisper import WhisperModel
            t0 = time.time()
            _whisper_model = WhisperModel(
                WHISPER_MODEL_SIZE,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE,
            )
            log.info(f"whisper model loaded in {time.time()-t0:.1f}s")
    return _whisper_model


# ── Auth ────────────────────────────────────────────────────────────────────


def check_auth(req: Request) -> None:
    if not SERVICE_TOKEN:
        return
    got = req.headers.get("authorization", "")
    if got != f"Bearer {SERVICE_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


# ── Endpoints ──────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return {"status": "ok", "time": time.time()}


@app.get("/info")
def info():
    return {
        "whisper_model": WHISPER_MODEL_SIZE,
        "whisper_compute": WHISPER_COMPUTE_TYPE,
        "whisper_language_default": WHISPER_LANGUAGE_DEFAULT,
        "piper_voice_default": PIPER_DEFAULT_VOICE,
        "piper_voices_dir": str(PIPER_VOICES_DIR),
        "piper_bin": PIPER_BIN,
        "model_loaded": _whisper_model is not None,
    }


@app.get("/voices")
def voices():
    if not PIPER_VOICES_DIR.exists():
        return {"voices": []}
    voices = []
    for onnx in PIPER_VOICES_DIR.glob("*.onnx"):
        name = onnx.stem
        meta = onnx.with_suffix(".onnx.json")
        info = {"name": name, "model_path": str(onnx)}
        if meta.exists():
            try:
                d = json.loads(meta.read_text())
                info["language"] = d.get("language", {}).get("code", "")
                info["sample_rate"] = d.get("audio", {}).get("sample_rate", 22050)
            except Exception:
                pass
        voices.append(info)
    return {"voices": sorted(voices, key=lambda v: v["name"])}


@app.post("/transcribe")
async def transcribe(
    request: Request,
    audio: UploadFile = File(...),
    language: Optional[str] = Form(None),
    beam_size: Optional[int] = Form(None),
):
    check_auth(request)
    t0 = time.time()

    # Save uploaded audio to a temp file (faster-whisper takes a path or file-like)
    with tempfile.NamedTemporaryFile(suffix=Path(audio.filename or "audio.webm").suffix or ".webm", delete=False) as f:
        data = await audio.read()
        f.write(data)
        tmp_path = f.name

    try:
        model = get_whisper()
        segments, info = model.transcribe(
            tmp_path,
            language=language or WHISPER_LANGUAGE_DEFAULT,
            beam_size=beam_size or WHISPER_BEAM_SIZE,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        text_parts = []
        for seg in segments:
            text_parts.append(seg.text)
        text = "".join(text_parts).strip()

        elapsed = time.time() - t0
        log.info(
            f"transcribe: audio_dur={info.duration:.1f}s, lang={info.language} "
            f"(prob={info.language_probability:.2f}), elapsed={elapsed:.1f}s, "
            f"rtf={elapsed/max(info.duration,0.001):.2f}, len={len(text)}"
        )
        return {
            "text": text,
            "language": info.language,
            "language_probability": info.language_probability,
            "duration": info.duration,
            "processing_time": elapsed,
        }
    except Exception as e:
        log.error(f"transcribe error: {e}")
        raise HTTPException(status_code=500, detail=f"transcribe_failed: {e}")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@app.post("/synthesize")
async def synthesize(request: Request):
    check_auth(request)
    body = await request.json()
    text = (body.get("text") or "").strip()
    voice = body.get("voice") or PIPER_DEFAULT_VOICE
    if not text:
        raise HTTPException(status_code=400, detail="text_required")
    if len(text) > 5000:
        raise HTTPException(status_code=400, detail="text_too_long_max_5000")

    voice_path = PIPER_VOICES_DIR / f"{voice}.onnx"
    if not voice_path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"voice_not_found: {voice} (check {PIPER_VOICES_DIR})",
        )

    t0 = time.time()
    try:
        # Piper reads text from stdin, writes WAV to stdout
        proc = subprocess.run(
            [PIPER_BIN, "--model", str(voice_path), "--output_raw"],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=60,
        )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace")[:500]
            log.error(f"piper failed: {err}")
            raise HTTPException(status_code=500, detail=f"piper_failed: {err}")

        # Convert raw PCM (int16, mono, sample_rate from voice config) to WAV
        # Piper's --output_raw gives PCM s16le at the voice's sample rate.
        sample_rate = 22050  # default; read from voice json if present
        meta_path = voice_path.with_suffix(".onnx.json")
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                sample_rate = int(meta.get("audio", {}).get("sample_rate", 22050))
            except Exception:
                pass

        # Wrap PCM in a minimal WAV container
        wav = pcm_to_wav(proc.stdout, sample_rate)
        elapsed = time.time() - t0
        log.info(
            f"synthesize: voice={voice}, chars={len(text)}, "
            f"audio_bytes={len(wav)}, elapsed={elapsed:.2f}s"
        )
        return Response(
            content=wav,
            media_type="audio/wav",
            headers={"X-Processing-Time": f"{elapsed:.2f}"},
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="piper_timeout")
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail=f"piper_binary_not_found: {PIPER_BIN}",
        )


# ── Helpers ─────────────────────────────────────────────────────────────────


def pcm_to_wav(pcm_bytes: bytes, sample_rate: int) -> bytes:
    import struct
    n_channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * n_channels * bits_per_sample // 8
    block_align = n_channels * bits_per_sample // 8
    data_size = len(pcm_bytes)
    riff_size = 36 + data_size

    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", riff_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<I", 16))  # PCM chunk size
    buf.write(struct.pack("<H", 1))   # audio format = PCM
    buf.write(struct.pack("<H", n_channels))
    buf.write(struct.pack("<I", sample_rate))
    buf.write(struct.pack("<I", byte_rate))
    buf.write(struct.pack("<H", block_align))
    buf.write(struct.pack("<H", bits_per_sample))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(pcm_bytes)
    return buf.getvalue()


# ── Main ───────────────────────────────────────────────────────────────────


def main():
    import uvicorn
    log.info(f"local-speech-service starting on {HOST}:{PORT}")
    log.info(f"  whisper model = {WHISPER_MODEL_SIZE}")
    log.info(f"  piper default voice = {PIPER_DEFAULT_VOICE}")
    log.info(f"  auth required = {bool(SERVICE_TOKEN)}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
