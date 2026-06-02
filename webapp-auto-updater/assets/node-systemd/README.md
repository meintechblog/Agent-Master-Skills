# Mode: `node-systemd` (turn-key, first-class)

Gold-standard auto-updater for a **Node web app on systemd/LXC** (bare http, Fastify, Express, …).
Mirrors the proven safety architecture of the `python-systemd` mode — adapted to Node. This is the
turn-key path for Node/LXC deployments (it replaces the old "Node = reference scaffolding only" gap).

## Architecture (same trust model as python-systemd)
- **App (unprivileged, `User=<service-user>`)** only DETECTS updates and WRITES a trigger file.
  Never touches `/opt` or restarts services. → `lib/updater.mjs`.
- **Root oneshot** (`<slug>-updater.service`, activated by `<slug>-updater.path` watching the
  trigger) does the privileged work: in-place `git checkout -B main <sha>` → preflight (`node
  --check`) → `npm ci` → `systemctl restart` → 90s healthcheck on `/api/health.self.commit_full`
  → rollback to the previous SHA on failure. → `scripts/update-apply.mjs`.
- **Recovery oneshot** (`<slug>-recovery.service`, `Before=<slug>.service`) reverts a
  crash-interrupted update on boot via a PENDING marker. → `scripts/recovery.mjs`.
- **Detection** is release-driven + GitHub compare-API (`status==="ahead"`), robust against
  orphaned tags. Same release conventions as everyone (`../../references/github-release-conventions.md`).

⚠️ **The rollback + recovery legs need a real fail-inject test before you trust them in production**
(ship a release whose `/api/health` never reports the target `commit_full`; confirm revert). The
mechanics are symmetric to the forward path but are newly authored for this mode.

## Why in-place checkout (not /opt blue-green)
For a git-clone Node deploy, `git checkout -B main <sha>` + `npm ci` is atomic enough and far
simpler than per-release venv/symlink trees (the python mode needs blue-green because pip installs
into a per-release venv). Rollback = checkout the recorded previous SHA. The PENDING marker +
recovery unit give the same crash-safety as the python symlink-flip + recovery.

## Files
| File | Role | Runs as |
|---|---|---|
| `lib/updater.mjs` | detection (compare-API) + `startApply()` writes trigger | app user |
| `scripts/update-apply.mjs` | privileged applier (checkout/npm/restart/health/rollback) | root (oneshot) |
| `scripts/recovery.mjs` | boot-time revert of interrupted update | root (oneshot) |
| `config/<slug>.service` | the Node app service | app user |
| `config/<slug>-updater.{service,path}` | root applier + trigger watcher | root |
| `config/<slug>-recovery.service` | boot recovery | root |
| `server-routes.snippet.mjs` | `/api/update/{check,apply,status}` + `/api/health` | — |
| `ui-badge.snippet.html` | update badge + one-click apply + status poll | — |

## Placeholders
`__APP_SLUG__`, `__GITHUB_REPO__`, `__APP_PORT__`, `__SERVICE_USER__`, `__ENV_PREFIX__`
(env-var prefix; e.g. `MYAPP` → `MYAPP_INSTALL_DIR`, `MYAPP_SERVICE`, `MYAPP_TRIGGER`, … override
the baked defaults). Optional `GITHUB_TOKEN` honored for API calls.

**Health override:** if your app already exposes the running SHA on a different route/field (e.g.
`/api/version` with `.sha`), set `<PREFIX>_HEALTH_URL` + `<PREFIX>_HEALTH_SHA_PATH` (dotted, default
`self.commit_full`) instead of adding `/api/health`.

**No build step:** the applier runs `npm ci --omit=dev` only — never `npm run build` (works for plain
Fastify/Express/http apps with no build). If your app DOES need a build, add it to the applier.

**Force checkout:** the applier uses `git checkout -f -B main <sha>` — it discards any local drift on
the deploy box (deploy dirs should never be hand-edited). Same for rollback/recovery.

## Manual wiring after generation
1. `lib/updater.mjs`, `scripts/update-apply.mjs`, `scripts/recovery.mjs` into your repo.
2. Wire the 4 routes from `server-routes.snippet.mjs`; `/api/health` must return
   `{ self: { commit_full } }`.
3. Include `ui-badge.snippet.html` in your page.
4. Install the 4 units (fix `ExecStart` to your entrypoint), `systemctl enable --now`
   `<slug>.service <slug>-updater.path <slug>-recovery.service`.
5. App must be a **git clone** at `/opt/<slug>` on `main`. The app user needs write to
   `/etc/<slug>` + `/var/lib/<slug>` (see `ReadWritePaths`).
6. Release conventions: real GitHub release, semver tag, `gh release create vX.Y.Z --target main`.

## Optional: nightly auto-apply
Manual-only by default (right for shared/multi-box services). For unattended nightly updates, add a
scheduler that calls `startApply()` at a fixed hour when `checkForUpdate().update_available`, gated
behind a config flag.

Detection: GitHub compare-API pattern. Installer/units: adapted from the
python-systemd mode.
