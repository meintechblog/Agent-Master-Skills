#!/usr/bin/env bash
# webapp-auto-updater — generator/installer
#
# Copies the templated updater assets into a TARGET repo, substituting the
# project-specific placeholders back into real values. Idempotent and
# re-runnable: overwrites generated files, never edits your hand-written
# app wiring (it prints the wiring steps instead).
#
# Usage:
#   install.sh --repo /path/to/target [options]
#
# Options (Python/systemd stack — the default & fully-supported path):
#   --repo PATH           Target repo root (required)
#   --stack STACK         python-systemd | nextjs   (default: auto-detect)
#   --app-slug SLUG       systemd/service + /opt//etc//var path name
#                           (default: target repo dir name)
#   --app-name NAME       Human-readable name for systemd Description=
#                           lines (default: app-slug)
#   --pkg-name NAME       Python import package under src/   (default: app-slug
#                           with '-' -> '_')
#   --github-repo OWNER/REPO   GitHub repo the updater polls /releases/latest on
#                           (required; e.g. <your-org>/<your-app>)
#   --service-user USER   systemd User=/Group= for the MAIN service
#                           (default: app-slug)
#   --app-port PORT       Port the app's /api/health listens on (default: 8080)
#   --tz TZ               IANA tz for the 04:00 nightly auto-update
#                           (default: Europe/Berlin)
#   --dry-run             Print what would be written, change nothing
#
# After it runs, follow the printed "MANUAL WIRING" block + read
# references/github-release-conventions.md before cutting your first release.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASSETS="$SKILL_DIR/assets"

REPO="" STACK="" APP_SLUG="" APP_NAME="" PKG_NAME="" GITHUB_REPO="" SERVICE_USER="" APP_PORT="8080" TZ_VAL="Europe/Berlin" DRY=0
LAUNCHD_LABEL="" ENV_PREFIX=""   # mac-launchd-node mode
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2;;
    --stack) STACK="$2"; shift 2;;
    --app-slug) APP_SLUG="$2"; shift 2;;
    --app-name) APP_NAME="$2"; shift 2;;
    --pkg-name) PKG_NAME="$2"; shift 2;;
    --github-repo) GITHUB_REPO="$2"; shift 2;;
    --service-user) SERVICE_USER="$2"; shift 2;;
    --app-port) APP_PORT="$2"; shift 2;;
    --tz) TZ_VAL="$2"; shift 2;;
    --launchd-label) LAUNCHD_LABEL="$2"; shift 2;;
    --env-prefix) ENV_PREFIX="$2"; shift 2;;
    --dry-run) DRY=1; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

[ -n "$REPO" ] || { echo "ERROR: --repo is required" >&2; exit 2; }
[ -d "$REPO" ] || { echo "ERROR: --repo '$REPO' not a directory" >&2; exit 2; }
REPO="$(cd "$REPO" && pwd)"

# Stack auto-detect
if [ -z "$STACK" ]; then
  if [ -f "$REPO/pyproject.toml" ] || ls "$REPO"/src/*/__init__.py >/dev/null 2>&1; then STACK="python-systemd";
  elif [ -f "$REPO/package.json" ]; then STACK="nextjs";
  else echo "ERROR: cannot auto-detect stack; pass --stack" >&2; exit 2; fi
fi
echo "stack: $STACK"

# Defaults derived from repo
[ -n "$APP_SLUG" ] || APP_SLUG="$(basename "$REPO")"
[ -n "$APP_NAME" ] || APP_NAME="$APP_SLUG"
[ -n "$SERVICE_USER" ] || SERVICE_USER="$APP_SLUG"
[ -n "$GITHUB_REPO" ] || { echo "ERROR: --github-repo OWNER/REPO is required" >&2; exit 2; }

if [ "$STACK" = "python-systemd" ]; then
  [ -n "$PKG_NAME" ] || PKG_NAME="$(echo "$APP_SLUG" | tr '-' '_')"
else
  PKG_NAME="${PKG_NAME:-$APP_SLUG}"
fi

echo "  app-slug:     $APP_SLUG"
echo "  pkg-name:     $PKG_NAME"
echo "  github-repo:  $GITHUB_REPO"
echo "  service-user: $SERVICE_USER"
echo "  app-port:     $APP_PORT"
echo "  tz:           $TZ_VAL"
[ "$DRY" = 1 ] && echo "  (DRY RUN — no files written)"

# Reverse-substitute placeholders -> real values, write to dest.
emit() { # emit <src-template> <dest>
  local src="$1" dest="$2"
  echo "  -> ${dest#$REPO/}"
  [ "$DRY" = 1 ] && return 0
  mkdir -p "$(dirname "$dest")"
  sed \
    -e "s#__GITHUB_REPO__#${GITHUB_REPO}#g" \
    -e "s#__SERVICE_USER__#${SERVICE_USER}#g" \
    -e "s#__APP_PORT__#${APP_PORT}#g" \
    -e "s#__TZ__#${TZ_VAL}#g" \
    -e "s#__PKG_NAME__#${PKG_NAME}#g" \
    -e "s#__APP_NAME__#${APP_NAME}#g" \
    -e "s#__APP_SLUG__#${APP_SLUG}#g" \
    "$src" > "$dest"
}

# --- node-systemd mode (Node app on systemd/LXC — turn-key, first-class) ---
if [ "$STACK" = "node-systemd" ]; then
  [ -n "$ENV_PREFIX" ] || ENV_PREFIX="$(echo "$APP_SLUG" | tr 'a-z-' 'A-Z_')"
  echo "  env-prefix:    $ENV_PREFIX"
  echo "  ⚠ rollback + recovery legs need a fail-inject test — see assets/node-systemd/README.md"
  NS="$ASSETS/node-systemd"
  emit_node() {
    local src="$1" dest="$2"
    echo "  -> ${dest#$REPO/}"
    [ "$DRY" = 1 ] && return 0
    mkdir -p "$(dirname "$dest")"
    sed \
      -e "s#__GITHUB_REPO__#${GITHUB_REPO}#g" \
      -e "s#__SERVICE_USER__#${SERVICE_USER}#g" \
      -e "s#__ENV_PREFIX__#${ENV_PREFIX}#g" \
      -e "s#__APP_PORT__#${APP_PORT}#g" \
      -e "s#__APP_SLUG__#${APP_SLUG}#g" \
      "$src" > "$dest"
  }
  emit_node "$NS/lib/updater.mjs"            "$REPO/lib/updater.mjs"
  emit_node "$NS/scripts/update-apply.mjs"   "$REPO/scripts/update-apply.mjs"
  emit_node "$NS/scripts/recovery.mjs"       "$REPO/scripts/recovery.mjs"
  emit_node "$NS/config/__APP_SLUG__.service"          "$REPO/config/${APP_SLUG}.service"
  emit_node "$NS/config/__APP_SLUG__-updater.service"  "$REPO/config/${APP_SLUG}-updater.service"
  emit_node "$NS/config/__APP_SLUG__-updater.path"     "$REPO/config/${APP_SLUG}-updater.path"
  emit_node "$NS/config/__APP_SLUG__-recovery.service" "$REPO/config/${APP_SLUG}-recovery.service"
  emit_node "$NS/server-routes.snippet.mjs"  "$REPO/updater-server-routes.snippet.mjs"
  emit_node "$NS/ui-badge.snippet.html"      "$REPO/updater-ui-badge.snippet.html"
  cat <<EOF

DONE (node-systemd). ⚠ FAIL-INJECT TEST the rollback + recovery legs before trusting them.
MANUAL WIRING:
  - Wire the 4 routes from updater-server-routes.snippet.mjs; /api/health MUST return
    { self: { commit_full } }.
  - Include updater-ui-badge.snippet.html in your page.
  - Fix ExecStart in config/${APP_SLUG}.service to your entrypoint.
  - Install units: cp config/${APP_SLUG}{,-updater,-recovery}.service config/${APP_SLUG}-updater.path
    /etc/systemd/system/ ; systemctl daemon-reload ;
    systemctl enable --now ${APP_SLUG}.service ${APP_SLUG}-updater.path ${APP_SLUG}-recovery.service
  - App = git clone at /opt/${APP_SLUG} on 'main'; app user needs write to /etc/${APP_SLUG} + /var/lib/${APP_SLUG}.
  - Release conventions — references/github-release-conventions.md (real release, semver, --target main).
  Full mode doc: assets/node-systemd/README.md
EOF
  exit 0
fi

# --- mac-launchd-node mode (Node app on macOS via user launchd) ---
if [ "$STACK" = "mac-launchd-node" ]; then
  [ -n "$LAUNCHD_LABEL" ] || { echo "ERROR: --launchd-label is required for mac-launchd-node (e.g. com.you.myapp)" >&2; exit 2; }
  [ -n "$ENV_PREFIX" ] || ENV_PREFIX="$(echo "$APP_SLUG" | tr 'a-z-' 'A-Z_')"
  echo "  launchd-label: $LAUNCHD_LABEL"
  echo "  env-prefix:    $ENV_PREFIX"
  echo "  ⚠ rollback-on-failure leg is UNTESTED — see assets/mac-launchd-node/README.md"
  MA="$ASSETS/mac-launchd-node"
  emit_mac() { # like emit() but with launchd/env-prefix tokens too
    local src="$1" dest="$2"
    echo "  -> ${dest#$REPO/}"
    [ "$DRY" = 1 ] && return 0
    mkdir -p "$(dirname "$dest")"
    sed \
      -e "s#__GITHUB_REPO__#${GITHUB_REPO}#g" \
      -e "s#__LAUNCHD_LABEL__#${LAUNCHD_LABEL}#g" \
      -e "s#__ENV_PREFIX__#${ENV_PREFIX}#g" \
      -e "s#__APP_PORT__#${APP_PORT}#g" \
      -e "s#__APP_SLUG__#${APP_SLUG}#g" \
      "$src" > "$dest"
  }
  emit_mac "$MA/lib/updater.mjs"               "$REPO/lib/updater.mjs"
  emit_mac "$MA/scripts/update-apply.mjs"      "$REPO/scripts/update-apply.mjs"
  emit_mac "$MA/server-routes.snippet.mjs"     "$REPO/updater-server-routes.snippet.mjs"
  emit_mac "$MA/ui-badge.snippet.html"         "$REPO/updater-ui-badge.snippet.html"
  cat <<EOF

DONE (mac-launchd-node). ⚠ ROLLBACK LEG UNTESTED — do a fail-inject test before trusting it.
MANUAL WIRING:
  - Wire the 3 routes from updater-server-routes.snippet.mjs into your HTTP server.
    /api/health MUST return { self: { commit_full } } (full SHA of running HEAD).
  - Include updater-ui-badge.snippet.html in your page.
  - Confirm launchd label: launchctl print gui/\$(id -u)/$LAUNCHD_LABEL
  - App must be a git clone on 'main' (applier does git checkout -B main <sha>).
  - Release conventions identical — references/github-release-conventions.md (real release,
    semver tag, --target main). Detection is release-gated + compare-API-robust.
  Full mode doc: assets/mac-launchd-node/README.md
EOF
  exit 0
fi

if [ "$STACK" != "python-systemd" ]; then
  echo
  echo "Next.js stack: see references/nextjs-notes.md — the JS deploy/restart path"
  echo "(pm2 or systemd + 'npm ci && npm run build') differs enough that it ships as"
  echo "documented reference scaffolding rather than blind file emission. Open that"
  echo "doc and adapt assets/nextjs/* to your process manager."
  exit 0
fi

SA="$ASSETS/python-systemd"
DST_PKG="$REPO/src/$PKG_NAME"

# 1. updater/ (non-privileged, runs as main service user)
for f in "$SA"/updater/*.py;       do emit "$f" "$DST_PKG/updater/$(basename "$f")"; done
# 2. updater_root/ (privileged, root systemd oneshot)
for f in "$SA"/updater_root/*.py;  do emit "$f" "$DST_PKG/updater_root/$(basename "$f")"; done
# 3. support modules imported by updater_root (only if target lacks its own)
for f in recovery.py releases.py state_file.py; do
  if [ -f "$SA/$f" ]; then
    if [ -f "$DST_PKG/$f" ]; then echo "  = keep existing $PKG_NAME/$f (not overwritten)";
    else emit "$SA/$f" "$DST_PKG/$f"; fi
  fi
done
# 4. webapp route module
emit "$SA/webapp/routes_updater.py" "$DST_PKG/webapp/routes_updater.py"
# 5. frontend
emit "$SA/static/software_page.js" "$DST_PKG/static/software_page.js"
# 6. systemd units (filenames carry the slug)
emit "$SA/config/__APP_SLUG__-updater.service"  "$REPO/config/${APP_SLUG}-updater.service"
emit "$SA/config/__APP_SLUG__-updater.path"     "$REPO/config/${APP_SLUG}-updater.path"
emit "$SA/config/__APP_SLUG__-recovery.service" "$REPO/config/${APP_SLUG}-recovery.service"
emit "$SA/config/__APP_SLUG__.service"          "$REPO/config/${APP_SLUG}.service"
# 7. install/deploy helpers (reference copies — review before trusting on a live box)
emit "$SA/scripts/install.sh" "$REPO/scripts/updater-install.reference.sh"
emit "$SA/scripts/deploy.sh"  "$REPO/scripts/updater-deploy.reference.sh"

cat <<EOF

DONE (generated files above). MANUAL WIRING — do these by hand:

  webapp/app.py:
    from $PKG_NAME.webapp.routes_updater import register_update_routes
    register_update_routes(app, config_path=..., ...)   # see routes_updater.py top

  __main__.py:
    - start the GitHub release scheduler task on boot
    - start the nightly auto_update task (updater/auto_update.py)
    - cancel both on shutdown
    - pass an _on_update_available callback that surfaces the SHA to the UI
    (mirror the wiring documented in references/architecture.md)

  pyproject.toml:
    - confirm [project].name == "$APP_SLUG" and [project].version is semver
      (the updater reads the running version via importlib.metadata)

  Frontend: include static/software_page.js in your "Software"/settings page
    and expose the /api/update/* + /api/version routes it calls.

  Deploy model: target box must be a GIT CLONE (blue-green via install.sh),
    not rsync. Set update.enabled=true and update.github_repo=$GITHUB_REPO
    in config.yaml.

NEXT: read references/github-release-conventions.md BEFORE cutting a release.
Cheat sheet: merge feature -> bump pyproject version -> push main ->
  gh release create vX.Y.Z --target main  -> the app pulls on next check / 04:00.
EOF
