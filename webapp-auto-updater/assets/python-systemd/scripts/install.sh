#!/usr/bin/env bash
# Web-App Auto-Updater — One-Line Installer
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/__GITHUB_REPO__/main/install.sh | bash
#
# What it does:
#   1. Creates the service user
#   2. Installs Python 3 + venv + git
#   3. Clones the repo (or pulls if exists)
#   4. Creates venv and installs package
#   5. Creates default config if missing
#   6. Installs and starts systemd service
#
# Requirements: Debian 12+ / Ubuntu 22.04+, root access
#
set -euo pipefail

REPO="https://github.com/__GITHUB_REPO__.git"
INSTALL_DIR="/opt/__APP_SLUG__"
CONFIG_DIR="/etc/__APP_SLUG__"
SERVICE_USER="__SERVICE_USER__"
SERVICE_NAME="__APP_SLUG__"
APP_PORT="__APP_PORT__"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}>>>${NC} $1"; }
ok()    { echo -e "${GREEN} ✓${NC} $1"; }
fail()  { echo -e "${RED} ✗ $1${NC}"; exit 1; }

# --- Pre-flight ---
echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  __APP_SLUG__ — Installer${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
echo ""

[ "$(id -u)" -eq 0 ] || fail "Must run as root"
command -v apt-get >/dev/null 2>&1 || fail "apt-get not found — Debian/Ubuntu required"

# Check if the app's listen port is already in use
if ss -tlnp 2>/dev/null | grep -q ":${APP_PORT} "; then
    echo ""
    echo -e "${BLUE}  Note: Port ${APP_PORT} is currently in use.${NC}"
    ss -tlnp 2>/dev/null | grep ":${APP_PORT} "
    echo ""
    echo -e "  The app needs port ${APP_PORT}. If this is a previous installation,"
    echo -e "  it will be restarted automatically. Otherwise stop the conflicting service first."
    echo ""
fi

# --- Step 1: System dependencies ---
info "Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git >/dev/null 2>&1
ok "Python 3, venv, git installed"

# --- Step 2: Service user ---
if id "$SERVICE_USER" &>/dev/null; then
    ok "User $SERVICE_USER exists"
else
    info "Creating service user..."
    useradd -r -s /usr/sbin/nologin "$SERVICE_USER"
    ok "User $SERVICE_USER created"
fi

# --- Step 3: Clone or update repo ---
# Blue-green layout: $INSTALL_DIR may be a symlink pointing into
# $RELEASES_ROOT/current/. Existing `-d $INSTALL_DIR/.git` works transparently
# because bash's `-d` follows symlinks. Fresh installs create the blue-green
# layout from the start; existing flat installs are migrated in Step 3a below.
RELEASES_ROOT="${INSTALL_DIR}-releases"

if [ -d "$INSTALL_DIR/.git" ]; then
    # Works for both flat and blue-green (symlink resolves transparently).
    # Use safe.directory='*' inline so git stops refusing to operate on
    # the service-user-owned worktree while running as root (git 2.35+
    # "dubious ownership" check). Scoped per-command — no persistent
    # git config side effects.
    info "Updating existing installation..."
    cd "$INSTALL_DIR"
    git -c safe.directory='*' fetch origin
    git -c safe.directory='*' reset --hard origin/main
    ok "Updated to latest"
elif [ -L "$INSTALL_DIR" ]; then
    fail "install_root $INSTALL_DIR is a symlink but target has no .git (corrupt layout?)"
elif [ ! -e "$INSTALL_DIR" ]; then
    # Fresh install: create blue-green layout from the start
    info "Fresh install — creating blue-green layout..."
    mkdir -p "$RELEASES_ROOT"
    SHORT_SHA=$(git ls-remote "$REPO" HEAD 2>/dev/null | awk '{print substr($1,1,7)}' || echo "bootstrap")
    if [ -z "$SHORT_SHA" ]; then
        SHORT_SHA="bootstrap"
    fi
    RELEASE_NAME="bootstrap-${SHORT_SHA}"
    RELEASE_DIR="${RELEASES_ROOT}/${RELEASE_NAME}"
    git clone "$REPO" "$RELEASE_DIR"
    ln -sfn "$RELEASE_DIR" "${RELEASES_ROOT}/current"
    ln -sfn "${RELEASES_ROOT}/current" "$INSTALL_DIR"
    ok "Fresh blue-green layout at $RELEASE_DIR"
else
    fail "install_root $INSTALL_DIR exists but is not a repo and not a symlink — manual cleanup needed"
fi

# --- Step 3a: Migrate flat layout to blue-green ---
# If $INSTALL_DIR is still a real directory (not a symlink) after Step 3,
# we have a legacy flat layout that needs migration. Refuse on a dirty
# tree so the user doesn't silently lose uncommitted local edits.
if [ ! -L "$INSTALL_DIR" ] && [ -d "$INSTALL_DIR/.git" ]; then
    info "Detected flat layout — migrating to blue-green..."
    cd "$INSTALL_DIR"
    DIRTY=$(git status --porcelain 2>/dev/null || echo "")
    if [ -n "$DIRTY" ]; then
        echo ""
        echo -e "${RED}  MIGRATION REFUSED: dirty working tree${NC}"
        echo ""
        echo "  Uncommitted changes in $INSTALL_DIR:"
        git status --short | head -30
        echo ""
        echo "  Resolve manually before re-running install.sh:"
        echo "    ssh root@<lxc>"
        echo "    cd $INSTALL_DIR"
        echo "    git status"
        echo "    # commit/stash/discard as appropriate"
        echo ""
        exit 1
    fi
    VERSION=$(git describe --tags --always 2>/dev/null || echo "0.0")
    SHORT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "nosha")
    # Normalize: strip any leading 'v', then prepend 'v' so we always end up with
    # "v7.0-abc1234" and never "vv7.0-abc1234".
    VERSION="${VERSION#v}"
    RELEASE_NAME="v${VERSION}-${SHORT_SHA}"
    RELEASE_DIR="${RELEASES_ROOT}/${RELEASE_NAME}"

    mkdir -p "$RELEASES_ROOT"
    # Step out of the dir we're about to rename (bash holds no fd here but
    # cwd on $INSTALL_DIR would block the rename on some filesystems).
    cd /
    mv "$INSTALL_DIR" "$RELEASE_DIR"
    ln -sfn "$RELEASE_DIR" "${RELEASES_ROOT}/current"
    ln -sfn "${RELEASES_ROOT}/current" "$INSTALL_DIR"
    ok "Migrated to $RELEASE_DIR"
elif [ -L "$INSTALL_DIR" ]; then
    ok "Blue-green layout already in place ($(readlink -f "$INSTALL_DIR"))"
fi

# --- Step 4: Python venv + install ---
info "Setting up Python environment..."
cd "$INSTALL_DIR"

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e .
ok "Package installed in venv"

# --- Step 5: Config ---
mkdir -p "$CONFIG_DIR"

if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    info "Creating default config..."
    cat > "$CONFIG_DIR/config.yaml" << 'YAML'
# __APP_SLUG__ Configuration (example — edit to match your app)
# Docs: https://github.com/__GITHUB_REPO__

# Upstream service this app talks to (optional — adjust or remove)
upstream:
  host: "192.0.2.10"      # your upstream service IP
  port: 8080              # your upstream service port

# Web dashboard
webapp:
  port: 80

# Logging
log_level: INFO
YAML
    ok "Default config created at $CONFIG_DIR/config.yaml"
    echo ""
    echo -e "${BLUE}  Edit the config to match your setup:${NC}"
    echo -e "  nano $CONFIG_DIR/config.yaml"
    echo ""
else
    ok "Config exists at $CONFIG_DIR/config.yaml"
fi

# --- Step 6: Permissions ---
# Follow symlink to the real release directory so chown -R reaches every file
# in the (possibly deeply nested) release dir. On a flat layout readlink -f
# returns $INSTALL_DIR unchanged, so this is a no-op there.
REAL_INSTALL=$(readlink -f "$INSTALL_DIR")
chown -R "$SERVICE_USER:$SERVICE_USER" "$REAL_INSTALL"
chown -R "$SERVICE_USER:$SERVICE_USER" "$CONFIG_DIR"
# Cosmetic: set symlink link ownership (does not affect access, symlinks
# themselves ignore owner). -h prevents dereferencing the link.
if [ -L "$INSTALL_DIR" ]; then
    chown -h "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR" 2>/dev/null || true
fi
if [ -L "${RELEASES_ROOT}/current" ]; then
    chown -h "$SERVICE_USER:$SERVICE_USER" "${RELEASES_ROOT}/current" 2>/dev/null || true
fi
ok "Permissions set"

# --- Step 6a: State + backups dirs ---
# /var/lib/__APP_SLUG__/ holds the PENDING marker and last-boot-success
# marker. /var/lib/__APP_SLUG__/backups/ will hold venv tarballs written
# by the privileged updater.
#
# Mode 2775 = rwx for root owner, rwx for the service-user group, r-x for
# other, plus the setgid bit so new files inherit the service-user group.
# This lets BOTH root (updater) and the service user (main service) write
# into these dirs.
info "Creating state and backup directories..."
install -d -o root -g "$SERVICE_USER" -m 2775 /var/lib/__APP_SLUG__
install -d -o root -g "$SERVICE_USER" -m 2775 /var/lib/__APP_SLUG__/backups
ok "State dir /var/lib/__APP_SLUG__/ ready"

# --- Step 6b: Update protocol file permissions ---
# update-trigger.json: mode 0664, owner root:<service-user>.
#   The main service writes via tempfile+os.replace in
#   updater/trigger.py. root (updater.path consumer) reads and acts on it.
# update-status.json: mode 0644, owner root:root.
#   Only the root updater writes; everyone (including the service user)
#   reads via updater/status.py. World-readable is intentional — contents
#   are phase names + SHAs, no secrets.
#
# Both files are created empty on fresh installs so the permissions are
# correct from the first POST /api/update/start. On re-runs we only
# chown/chmod — we do NOT truncate, so an in-progress update survives a
# mid-flight install.sh re-run.
#
# NOTE: $CONFIG_DIR itself stays service-user-owned so the main service
# can create per-feature files (state.json, etc). We enforce ownership at
# the file level, not the directory level.
info "Setting update protocol file permissions..."
TRIGGER_FILE="$CONFIG_DIR/update-trigger.json"
STATUS_FILE="$CONFIG_DIR/update-status.json"

if [ ! -e "$TRIGGER_FILE" ]; then
    # Empty placeholder — NOT a valid trigger (schema rejects empty JSON),
    # safe to leave on disk. The main service overwrites atomically on
    # first POST /api/update/start.
    install -o root -g "$SERVICE_USER" -m 0664 /dev/null "$TRIGGER_FILE"
else
    chown "root:$SERVICE_USER" "$TRIGGER_FILE"
    chmod 0664 "$TRIGGER_FILE"
fi

if [ ! -e "$STATUS_FILE" ]; then
    install -o root -g root -m 0644 /dev/null "$STATUS_FILE"
else
    chown "root:root" "$STATUS_FILE"
    chmod 0644 "$STATUS_FILE"
fi
ok "Update protocol files permissioned (trigger 0664 root:$SERVICE_USER, status 0644 root:root)"

# --- Step 7: Systemd services (main + recovery + updater) ---
# Two units drive the privileged update flow:
#   __APP_SLUG__-updater.path     — watches update-trigger.json
#   __APP_SLUG__-updater.service  — Type=oneshot root helper that
#                                        runs the update state machine
# The .path unit is what we enable; it activates the .service on every
# PathModified event. The .service has no [Install] section because it
# is only ever spawned by the .path unit (not enabled directly).
info "Installing systemd services..."
cp "$INSTALL_DIR/config/__APP_SLUG__.service" /etc/systemd/system/
cp "$INSTALL_DIR/config/__APP_SLUG__-recovery.service" /etc/systemd/system/
cp "$INSTALL_DIR/config/__APP_SLUG__-updater.path" /etc/systemd/system/
cp "$INSTALL_DIR/config/__APP_SLUG__-updater.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl enable __APP_SLUG__-recovery.service
systemctl enable __APP_SLUG__-updater.path
# Start the .path unit immediately so it begins watching on install.
# The .service it activates is Type=oneshot and stays idle until the
# trigger file is modified by POST /api/update/start.
systemctl restart __APP_SLUG__-updater.path || \
    systemctl start __APP_SLUG__-updater.path
ok "Services installed and enabled (main + recovery + updater)"

# --- Step 8: Start ---
info "Starting service..."
systemctl restart "$SERVICE_NAME"
sleep 2

if systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "Service is running"
else
    echo ""
    echo -e "${RED}  Service failed to start. Check logs:${NC}"
    echo "  journalctl -u $SERVICE_NAME -n 20 --no-pager"
    echo ""
    exit 1
fi

# --- Done ---
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Installation complete!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo ""
echo "  Dashboard:  http://$(hostname -I | awk '{print $1}')"
echo "  Config:     $CONFIG_DIR/config.yaml"
echo "  Logs:       journalctl -u $SERVICE_NAME -f"
echo "  Status:     systemctl status $SERVICE_NAME"
echo ""
echo "  To update later:"
echo "    curl -fsSL https://raw.githubusercontent.com/__GITHUB_REPO__/main/install.sh | bash"
echo ""
