# Mode: `mac-launchd-node`

Auto-updater for a **Node web app that runs on macOS via a user `launchd` agent** (not LXC/systemd,
not root). Adapted from the reference implementation that a production webapp shipped as
release v0.2.0 — proven in production, with one untested leg (see ⚠️ below).

## ⚠️ UNTESTED CAVEAT
The **rollback-on-failure leg is UNTESTED in the field.** The forward path (detect → checkout →
`npm ci` → kickstart → health-match) was E2E-verified on the reference webapp (~5s real restart). The
rollback path (health-poll timeout → `git checkout -B main <prev>` → kickstart) is the same
primitives in reverse but has never been fired against a deliberately-broken release. **Before
trusting rollback in production, do a fail-inject test** (ship a release whose `/api/health` never
reports the target `commit_full`, confirm it reverts). Until then, treat one-click apply as
"forward works, rollback is best-effort."

## How it differs from `python-systemd`
| Aspect | python-systemd | mac-launchd-node |
|---|---|---|
| Restart | `systemctl restart` (root oneshot via `.path`) | `launchctl kickstart -k gui/<uid>/<label>` |
| Privilege | root separation | none (user-launchd) |
| Deploy | blue-green `/opt` + symlink flip | in-place `git checkout -B main <sha>` |
| Build | venv + pip | `npm ci --omit=dev` |
| Restart survival | runs outside the dying cgroup | detached `spawn(detached,stdio:ignore).unref()` worker |
| Health match | `/api/health` version | `/api/health.self.commit_full` (full-SHA) |

## Files
- `lib/updater.mjs` — `checkForUpdate()` (release-gated + compare-API `status==="ahead"`, robust to
  orphaned tags) and `startApply(targetSha)` (spawns the detached worker).
- `scripts/update-apply.mjs` — the **self-contained** detached applier. Imports NOTHING from the
  repo, because its own repo files get swapped under it during checkout. Sequence: anchor prev HEAD
  → `git checkout -B main <target>` → `npm ci` → `launchctl kickstart` → poll health → rollback on
  timeout.
- `server-routes.snippet.mjs` — the 3 routes (`/api/update/check`, `/api/update/apply`,
  `/api/health` with `self.commit_full`) to wire into your server. **`self.commit_full` is required.**
- `ui-badge.snippet.html` — "update available" badge + one-click apply (manual-only, 5-min poll).

## Placeholders
`__GITHUB_REPO__`, `__LAUNCHD_LABEL__` (e.g. `com.you.myapp`), `__APP_PORT__`, `__APP_SLUG__`,
`__ENV_PREFIX__` (env-var prefix, e.g. `MYAPP` → `MYAPP_GITHUB_REPO` / `_LAUNCHD_LABEL` / `_HEALTH_URL`
override the baked-in defaults at runtime). Optional `GITHUB_TOKEN` env is honored for API calls.

## Manual wiring after generation
1. Put `lib/updater.mjs` + `scripts/update-apply.mjs` in your repo root (`lib/`, `scripts/`).
2. Wire the 3 routes from `server-routes.snippet.mjs` into your HTTP server; ensure `/api/health`
   returns `{ self: { commit_full } }`.
3. Include `ui-badge.snippet.html` in your page.
4. Confirm your `launchd` label matches `__LAUNCHD_LABEL__` (`launchctl print gui/$(id -u)/<label>`).
5. The app must be a **git clone** on `main`; the applier does `git checkout -B main <sha>`.
6. Release conventions are identical — see `../../references/github-release-conventions.md`
   (real GitHub release, semver tag, `--target main`). Detection is release-gated, so you still need
   an actual release; compare-API only makes the "ahead?" check robust against orphaned tags.

Reference origin: a production Node/launchd webapp.
