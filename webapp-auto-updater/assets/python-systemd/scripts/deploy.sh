#!/usr/bin/env bash
# Deploy __APP_SLUG__ to LXC container (192.0.2.20)
# Usage: ./deploy.sh [--first-time]
set -euo pipefail

# NOTE (+): After install.sh runs the blue-green migration,
# /opt/__APP_SLUG__ is a symlink pointing to
# /opt/__APP_SLUG__-releases/current -> /opt/__APP_SLUG__-releases/<release>/.
# rsync follows the destination symlink and writes into the real release
# directory, so this script continues to work unchanged on migrated hosts.
#
# For a FIRST-TIME bootstrap on a new LXC, run install.sh on the LXC
# instead of --first-time here. --first-time is kept for backwards compat
# but no longer creates /opt/__APP_SLUG__ as a plain directory.

LXC_HOST="root@192.0.2.20"
REMOTE_DIR="/opt/__APP_SLUG__"
SERVICE="__APP_SLUG__"

echo "=== Deploying __APP_SLUG__ to $LXC_HOST ==="

# First-time setup (creates user, venv, apt deps — NOT the install dir)
if [[ "${1:-}" == "--first-time" ]]; then
    echo ">>> First-time setup..."
    echo " NOTE (+): Consider running install.sh on the LXC instead"
    echo "    for a clean blue-green bootstrap:"
    echo "      ssh $LXC_HOST 'curl -fsSL https://raw.githubusercontent.com/__GITHUB_REPO__/main/install.sh | bash'"
    echo ""

    ssh "$LXC_HOST" bash -s <<'SETUP'
set -euo pipefail

# Create service user (no login)
id __SERVICE_USER__ &>/dev/null || useradd -r -s /usr/sbin/nologin __SERVICE_USER__

# Create config dir only — do NOT mkdir /opt/__APP_SLUG__, which would
# interfere with the blue-green layout (where install_dir is a symlink
# managed by install.sh).
mkdir -p /etc/__APP_SLUG__

# Install Python + venv + rsync
apt-get update -qq && apt-get install -y -qq python3 python3-venv python3-pip git rsync

chown -R __SERVICE_USER__:__SERVICE_USER__ /etc/__APP_SLUG__

echo ">>> First-time prereqs done."
echo ">>> Run install.sh next to create the blue-green layout, then re-run deploy.sh without --first-time."
SETUP
    echo ""
    echo "First-time prereqs installed on $LXC_HOST."
    echo "Next step: ssh $LXC_HOST and run install.sh, then re-run ./deploy.sh (no flags)."
    exit 0
fi

# Sync source code (exclude dev files, .planning, tests, .git*)
# NOTE: We exclude both `.git/` (directory in main checkouts) and `.git`
# (file pointer in git worktrees). Without the file exclude, a deploy from
# a worktree would ship the dangling gitdir pointer to the LXC, breaking
# install.sh migration's `git describe` call with a nosha/v0.0 fallback name.
#: Capture short SHA from the dev-side git checkout into a
# COMMIT file that ships with the sync. On the LXC, `.git/` is excluded for
# size/security reasons, so `git rev-parse` cannot run there. The updater's
# get_commit_hash() falls back to reading this file. We write it into the
# source tree briefly, sync it, then remove it so the working tree stays clean.
COMMIT_SHORT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
echo "$COMMIT_SHORT" > src/__PKG_NAME__/COMMIT
trap 'rm -f src/__PKG_NAME__/COMMIT' EXIT

echo ">>> Syncing source code (commit=$COMMIT_SHORT)..."
rsync -avz --delete \
    --exclude '.git/' \
    --exclude '.git' \
    --exclude '.gitignore' \
    --exclude '.planning/' \
    --exclude '.claude/' \
    --exclude 'tests/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.venv/' \
    --exclude 'node_modules/' \
    --exclude '.pytest_cache/' \
    ./ "$LXC_HOST:$REMOTE_DIR/"

# Install package + copy service files
echo ">>> Installing package..."
ssh "$LXC_HOST" bash -s <<'INSTALL'
set -euo pipefail
cd /opt/__APP_SLUG__
.venv/bin/pip install -e . --quiet

# Update systemd units (main + recovery + updater, + / 45-04).
# The recovery + updater units are copied even if this is pre-migration —
# the files are harmless until install.sh enables them, and having them
# in place means the next install.sh run has no unit-file copy work left.
cp config/__APP_SLUG__.service /etc/systemd/system/
if [ -f config/__APP_SLUG__-recovery.service ]; then
    cp config/__APP_SLUG__-recovery.service /etc/systemd/system/
fi
# privileged updater path+service pair
if [ -f config/__APP_SLUG__-updater.path ]; then
    cp config/__APP_SLUG__-updater.path /etc/systemd/system/
fi
if [ -f config/__APP_SLUG__-updater.service ]; then
    cp config/__APP_SLUG__-updater.service /etc/systemd/system/
fi
systemctl daemon-reload
# Enable recovery unit if present (idempotent; safe on pre-migration hosts).
if [ -f /etc/systemd/system/__APP_SLUG__-recovery.service ]; then
    systemctl enable __APP_SLUG__-recovery.service 2>/dev/null || true
fi
#: enable + start the updater.path watcher (idempotent).
# The .service has no [Install] and is only activated by the .path.
if [ -f /etc/systemd/system/__APP_SLUG__-updater.path ]; then
    systemctl enable __APP_SLUG__-updater.path 2>/dev/null || true
    systemctl restart __APP_SLUG__-updater.path 2>/dev/null || \
        systemctl start __APP_SLUG__-updater.path 2>/dev/null || true
fi
INSTALL

# Restart service
echo ">>> Restarting service..."
ssh "$LXC_HOST" "systemctl restart $SERVICE"

# Wait and check status
sleep 2
echo ">>> Service status:"
ssh "$LXC_HOST" "systemctl status $SERVICE --no-pager -l" || true

echo ""
echo "=== Deploy complete ==="
echo "Dashboard: http://192.0.2.20"
echo "Logs:      ssh $LXC_HOST journalctl -u $SERVICE -f"
