#!/usr/bin/env bash
# local-speech-service installer
# Installs faster-whisper + Piper TTS as a launchd-managed HTTP service on Apple Silicon.

set -euo pipefail

SKILL_DIR="${SKILL_DIR:-$HOME/.claude/skills/local-speech-service}"
TEMPLATES="$SKILL_DIR/templates"

# ── defaults ────────────────────────────────────────────────────────────────
INSTALL_DIR="$HOME/.local-speech-service"
WHISPER_MODEL="large-v3-turbo"
WHISPER_LANG="de"
DEFAULT_VOICE="de_DE-thorsten-medium"
EXTRA_VOICES=""  # comma-separated, e.g. "de_DE-kerstin-low,de_DE-ramona-low"
PORT="8765"
HOST="0.0.0.0"
CORS_ORIGINS="*"
SERVICE_TOKEN=""
SKIP_LAUNCHD=0
SKIP_PIPER=0
SKIP_WHISPER_PRELOAD=0
FORCE=0

usage() {
  cat <<EOF
Usage: $0 [options]

Options:
  --install-dir <path>    Where to put venv + service files. Default: ~/.local-speech-service
  --whisper-model <name>  Whisper model size. Default: large-v3-turbo
                          Options: tiny | base | small | medium | large-v3-turbo | large-v3
  --whisper-lang <code>   Default language hint (still auto-detected if not set). Default: de
  --default-voice <name>  Piper default voice. Default: de_DE-thorsten-medium
  --extra-voices <csv>    Extra Piper voices to download (comma-separated).
                          Example: "de_DE-kerstin-low,de_DE-ramona-low"
  --port <n>              HTTP port. Default: 8765
  --host <ip>             Bind host. Default: 0.0.0.0 (LAN-accessible).
                          Use 127.0.0.1 to restrict to localhost only.
  --cors-origins <csv>    Allowed Origins for browser. Default: "*"
                          Example: "http://192.0.2.10,http://localhost:3000"
  --service-token <val>   Bake a Bearer-token into the service (recommended for non-LAN exposure)
  --skip-launchd          Don't install launchd job; print manual start command instead
  --skip-piper            Skip Piper install (already there or use TTS later)
  --skip-whisper-preload  Skip the model warmup at install (faster install, slower first request)
  --force                 Overwrite existing install
  -h, --help              Show this help

Examples:
  # Default install: large-v3-turbo + thorsten-medium DE, LAN-open, launchd-managed
  $0

  # Restrict to localhost only:
  $0 --host 127.0.0.1

  # Lightweight install for resource-constrained Macs:
  $0 --whisper-model small --default-voice de_DE-thorsten-medium

  # Multi-voice install + bearer-protected:
  $0 --extra-voices "de_DE-kerstin-low,de_DE-ramona-low" \\
     --service-token "\$(openssl rand -hex 16)"
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    --whisper-model) WHISPER_MODEL="$2"; shift 2 ;;
    --whisper-lang) WHISPER_LANG="$2"; shift 2 ;;
    --default-voice) DEFAULT_VOICE="$2"; shift 2 ;;
    --extra-voices) EXTRA_VOICES="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --cors-origins) CORS_ORIGINS="$2"; shift 2 ;;
    --service-token) SERVICE_TOKEN="$2"; shift 2 ;;
    --skip-launchd) SKIP_LAUNCHD=1; shift ;;
    --skip-piper) SKIP_PIPER=1; shift ;;
    --skip-whisper-preload) SKIP_WHISPER_PRELOAD=1; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

ARCH=$(uname -m)
if [[ "$ARCH" != "arm64" && "$ARCH" != "x86_64" ]]; then
  echo "unsupported arch: $ARCH (need arm64 or x86_64)" >&2; exit 1
fi

VOICES_DIR="$INSTALL_DIR/voices"
VENV_DIR="$INSTALL_DIR/venv"
VENV_PYTHON="$VENV_DIR/bin/python"
SERVER_PY="$INSTALL_DIR/server.py"
PLIST="$HOME/Library/LaunchAgents/com.local-speech-service.plist"

echo "→ Install dir:        $INSTALL_DIR"
echo "→ Architecture:       $ARCH"
echo "→ Whisper model:      $WHISPER_MODEL (lang=$WHISPER_LANG)"
echo "→ Piper voice (def):  $DEFAULT_VOICE"
[[ -n "$EXTRA_VOICES" ]] && echo "→ Extra voices:       $EXTRA_VOICES"
echo "→ Port / host:        $PORT / $HOST"
echo "→ CORS origins:       $CORS_ORIGINS"
echo "→ Auth token:         $([[ -n "$SERVICE_TOKEN" ]] && echo "set" || echo "(open)")"
echo "→ Force overwrite:    $FORCE"
echo "→ launchd install:    $([[ $SKIP_LAUNCHD -eq 1 ]] && echo "skip" || echo "yes")"
echo

# ── pre-flight ──────────────────────────────────────────────────────────────
require() {
  command -v "$1" >/dev/null 2>&1 || { echo "missing dependency: $1" >&2; exit 1; }
}
require curl
require launchctl

# Pick the best Python (prefer 3.10+ — needed because of new typing syntax / faster-whisper req's).
PYTHON_BIN=""
for cand in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
            /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11 \
            python3.13 python3.12 python3.11 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    ver=$("$cand" -c "import sys; print(1 if sys.version_info >= (3,10) else 0)" 2>/dev/null || echo 0)
    if [[ "$ver" == "1" ]]; then
      PYTHON_BIN="$cand"
      break
    fi
  fi
done
if [[ -z "$PYTHON_BIN" ]]; then
  echo "  ⚠️  No Python 3.10+ found. Falling back to system python3 ($(python3 --version 2>&1))."
  echo "     Install one via 'brew install python@3.12' for best results."
  PYTHON_BIN="python3"
fi
echo "→ Python:             $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"

# ── install dir ─────────────────────────────────────────────────────────────
if [[ -d "$INSTALL_DIR" && $FORCE -eq 0 ]]; then
  echo "  install-dir exists. Use --force to wipe, or keep and let the script re-use the venv."
fi
mkdir -p "$INSTALL_DIR" "$VOICES_DIR"

# ── Python venv + deps ──────────────────────────────────────────────────────
# Recreate venv if it's missing OR --force OR wrong python version
NEEDS_VENV=0
[[ ! -x "$VENV_PYTHON" ]] && NEEDS_VENV=1
[[ $FORCE -eq 1 ]] && NEEDS_VENV=1
if [[ $NEEDS_VENV -eq 0 && -x "$VENV_PYTHON" ]]; then
  current_ver=$("$VENV_PYTHON" -c "import sys; print(1 if sys.version_info >= (3,10) else 0)" 2>/dev/null || echo 0)
  if [[ "$current_ver" != "1" ]]; then
    echo "  existing venv has unsupported Python — recreating"
    NEEDS_VENV=1
  fi
fi
if [[ $NEEDS_VENV -eq 1 ]]; then
  echo "→ Creating venv with ${PYTHON_BIN}..."
  rm -rf "$VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

echo "→ Installing Python deps (this can take a few minutes)..."
"$VENV_PYTHON" -m pip install --upgrade pip --quiet
PIP_PKGS=(
  "faster-whisper>=1.0.3"
  "fastapi>=0.115"
  "uvicorn[standard]>=0.32"
  "python-multipart>=0.0.12"
)
if [[ $SKIP_PIPER -eq 0 ]]; then
  # piper-tts is native arm64 on Apple Silicon (rhasspy/piper github releases were unreliable)
  PIP_PKGS+=("piper-tts>=1.4.0")
fi
"$VENV_PYTHON" -m pip install --quiet "${PIP_PKGS[@]}"

# ── Piper binary (from venv) ─────────────────────────────────────────────────
PIPER_BIN_PATH=""
if [[ $SKIP_PIPER -eq 0 ]]; then
  PIPER_BIN_PATH="$VENV_DIR/bin/piper"
  if [[ ! -x "$PIPER_BIN_PATH" ]]; then
    echo "  ERROR: piper-tts didn't install correctly — check pip output" >&2
    exit 1
  fi
  echo "→ Piper:              $PIPER_BIN_PATH (via pip)"
fi
[[ -z "$PIPER_BIN_PATH" ]] && PIPER_BIN_PATH="$VENV_DIR/bin/piper"

# ── Piper voices ────────────────────────────────────────────────────────────
download_voice() {
  local voice="$1"
  local target_onnx="$VOICES_DIR/${voice}.onnx"
  local target_json="$VOICES_DIR/${voice}.onnx.json"
  if [[ -f "$target_onnx" && -f "$target_json" ]]; then
    echo "  voice already present: $voice"
    return 0
  fi
  # huggingface path: rhasspy/piper-voices/<lang>/<locale>/<voice>/<quality>/<filename>
  # voice format: de_DE-thorsten-medium  → locale=de_DE, name=thorsten, quality=medium, lang=de
  local locale="${voice%-*-*}"           # de_DE
  local rest="${voice#${locale}-}"        # thorsten-medium
  local name="${rest%-*}"                 # thorsten
  local quality="${rest##*-}"             # medium
  local lang="${locale%_*}"               # de
  local base_url="https://huggingface.co/rhasspy/piper-voices/resolve/main/$lang/$locale/$name/$quality"
  echo "  downloading voice: $voice"
  curl -fsSL "$base_url/${voice}.onnx" -o "$target_onnx" || {
    echo "  failed to download $voice — check the name (locale-name-quality)" >&2
    rm -f "$target_onnx"; return 1
  }
  curl -fsSL "$base_url/${voice}.onnx.json" -o "$target_json" || {
    echo "  failed to download $voice metadata" >&2
    rm -f "$target_json"; return 1
  }
}

if [[ $SKIP_PIPER -eq 0 ]]; then
  echo "→ Downloading Piper voices…"
  download_voice "$DEFAULT_VOICE"
  if [[ -n "$EXTRA_VOICES" ]]; then
    IFS=',' read -ra EXTRA <<< "$EXTRA_VOICES"
    for v in "${EXTRA[@]}"; do
      [[ -n "$v" ]] && download_voice "$v" || true
    done
  fi
fi

# ── server.py ───────────────────────────────────────────────────────────────
echo "→ Writing server.py…"
cp "$TEMPLATES/server.py" "$SERVER_PY"
chmod +x "$SERVER_PY"

# ── plist ───────────────────────────────────────────────────────────────────
if [[ $SKIP_LAUNCHD -eq 0 ]]; then
  echo "→ Rendering launchd plist…"
  awk -v venv_python="$VENV_PYTHON" \
      -v install_dir="$INSTALL_DIR" \
      -v whisper_model="$WHISPER_MODEL" \
      -v whisper_lang="$WHISPER_LANG" \
      -v piper_bin="$PIPER_BIN_PATH" \
      -v voices_dir="$VOICES_DIR" \
      -v default_voice="$DEFAULT_VOICE" \
      -v port="$PORT" \
      -v host="$HOST" \
      -v cors_origins="$CORS_ORIGINS" \
  '{
    gsub(/__VENV_PYTHON__/, venv_python);
    gsub(/__INSTALL_DIR__/, install_dir);
    gsub(/__WHISPER_MODEL__/, whisper_model);
    gsub(/__WHISPER_LANG__/, whisper_lang);
    gsub(/__PIPER_BIN__/, piper_bin);
    gsub(/__VOICES_DIR__/, voices_dir);
    gsub(/__DEFAULT_VOICE__/, default_voice);
    gsub(/__PORT__/, port);
    gsub(/__HOST__/, host);
    gsub(/__CORS_ORIGINS__/, cors_origins);
    print
  }' "$TEMPLATES/plist.xml" > "$INSTALL_DIR/com.local-speech-service.plist"

  # add token env var to plist if set
  if [[ -n "$SERVICE_TOKEN" ]]; then
    # insert before closing </dict> of EnvironmentVariables (first </dict>)
    awk -v tok="$SERVICE_TOKEN" '
      /<\/dict>/ && !done {
        print "    <key>SPEECH_SERVICE_TOKEN</key>"
        print "    <string>" tok "</string>"
        done=1
      }
      { print }
    ' "$INSTALL_DIR/com.local-speech-service.plist" > "$INSTALL_DIR/com.local-speech-service.plist.tmp"
    mv "$INSTALL_DIR/com.local-speech-service.plist.tmp" "$INSTALL_DIR/com.local-speech-service.plist"
  fi
fi

# ── README ──────────────────────────────────────────────────────────────────
awk -v install_dir="$INSTALL_DIR" \
    -v port="$PORT" \
    -v whisper_model="$WHISPER_MODEL" \
    -v default_voice="$DEFAULT_VOICE" \
    -v voices_dir="$VOICES_DIR" \
'{
  gsub(/__INSTALL_DIR__/, install_dir);
  gsub(/__PORT__/, port);
  gsub(/__WHISPER_MODEL__/, whisper_model);
  gsub(/__DEFAULT_VOICE__/, default_voice);
  gsub(/__VOICES_DIR__/, voices_dir);
  print
}' "$TEMPLATES/readme.md" > "$INSTALL_DIR/README.md"

# ── Preload whisper model (optional) ────────────────────────────────────────
if [[ $SKIP_WHISPER_PRELOAD -eq 0 ]]; then
  echo "→ Pre-downloading whisper model ($WHISPER_MODEL)… this may take a few minutes the first time"
  "$VENV_PYTHON" -c "
from faster_whisper import WhisperModel
print('downloading…')
m = WhisperModel('$WHISPER_MODEL', device='auto', compute_type='int8')
print('model ready')
" || echo "  warning: model preload failed — will retry on first request"
fi

# ── launchd ────────────────────────────────────────────────────────────────
if [[ $SKIP_LAUNCHD -eq 0 ]]; then
  echo "→ Installing launchd job…"
  if launchctl print "gui/$(id -u)/com.local-speech-service" >/dev/null 2>&1; then
    echo "  unloading existing job…"
    launchctl bootout "gui/$(id -u)/com.local-speech-service" 2>/dev/null || true
  fi
  cp "$INSTALL_DIR/com.local-speech-service.plist" "$PLIST"
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "  job loaded as com.local-speech-service"

  # health-check
  echo "→ Waiting for service to come up…"
  for i in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
      echo "  ✅ service responding on port $PORT"
      break
    fi
    if [[ $i -eq 10 ]]; then
      echo "  ⚠️  service didn't come up — check $INSTALL_DIR/service.stderr.log"
    fi
  done
fi

# ── done ───────────────────────────────────────────────────────────────────
cat <<EOF

✅ local-speech-service installed.

Endpoints:
  Health:     http://$(hostname -s):$PORT/health
  Info:       http://$(hostname -s):$PORT/info
  Voices:     http://$(hostname -s):$PORT/voices
  Transcribe: POST http://$(hostname -s):$PORT/transcribe (multipart: audio=@file)
  Synthesize: POST http://$(hostname -s):$PORT/synthesize (json: {"text":"..."})

Files:        $INSTALL_DIR
Logs:         tail -f $INSTALL_DIR/service.log
README:       $INSTALL_DIR/README.md

EOF

if [[ $SKIP_LAUNCHD -eq 1 ]]; then
  cat <<EOF
launchd skipped. Start manually:
  $VENV_PYTHON $SERVER_PY

Or install later:
  cp $INSTALL_DIR/com.local-speech-service.plist ~/Library/LaunchAgents/
  launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/com.local-speech-service.plist
EOF
fi
