// Example Node webapp — self-update (Mac/launchd/Node-adaptiert, NICHT systemd).
//
// Eigenheiten dieses Stacks (bewusst anders als das webapp-auto-updater-Template):
//   - Die App läuft IN-PLACE direkt aus dem git-Checkout (launchd WorkingDirectory
//     == dieses Repo). Kein /opt-Blue-Green-Symlink, kein venv.
//   - user-launchd (gui/<uid>/__LAUNCHD_LABEL__), KEIN root.
//   - Restart via `launchctl kickstart -k`.
//   - "Update" == ein getaggtes GitHub-Release (nicht roher origin/main-HEAD).
//   - Manual-only: die App zeigt nur "Update verfügbar" und applied AUSSCHLIESSLICH
//     auf expliziten POST /api/update/apply. Kein nächtliches Auto-Update.
//
// Die eigentliche Apply-Mechanik läuft in scripts/update-apply.mjs als losgelöster
// (detached, unref'd) Prozess, damit sie den `kickstart -k` überlebt, der die App
// mitten im Update neu startet.

import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import fs from "node:fs/promises";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const execFileP = promisify(execFile);
const __dirname = path.dirname(fileURLToPath(import.meta.url));

export const REPO_DIR = path.resolve(__dirname, "..");
export const GITHUB_REPO = "__GITHUB_REPO__";
export const LAUNCHD_LABEL = "__LAUNCHD_LABEL__";
export const STATE_PATH = path.join(REPO_DIR, "data", "update-state.json");
const APPLY_SCRIPT = path.join(REPO_DIR, "scripts", "update-apply.mjs");

const CHECK_CACHE_MS = 10 * 60 * 1000;
let _checkCache = { data: null, at: 0 };
let _selfCache = null;

async function git(args) {
  const { stdout } = await execFileP("git", args, { cwd: REPO_DIR, timeout: 60_000 });
  return stdout.trim();
}

export function getLocalVersion() {
  try {
    const pkg = JSON.parse(readFileSync(path.join(REPO_DIR, "package.json"), "utf8"));
    return pkg.version || null;
  } catch {
    return null;
  }
}

async function getLocalCommit() {
  try {
    const full = await git(["rev-parse", "HEAD"]);
    return { full, short: full.slice(0, 7) };
  } catch {
    return { full: null, short: null };
  }
}

// version + commit + nearest tag of the running checkout. Cached in-process: the
// process restarts on a successful update, so a stale cache can't outlive a real
// version change.
export async function getSelfInfo() {
  if (_selfCache) return _selfCache;
  const commit = await getLocalCommit();
  let describe = null;
  try {
    describe = await git(["describe", "--tags", "--always"]);
  } catch {}
  _selfCache = {
    version: getLocalVersion(),
    commit: commit.short,
    commit_full: commit.full,
    describe,
  };
  return _selfCache;
}

export async function checkForUpdate(force = false) {
  const now = Date.now();
  if (!force && _checkCache.data && now - _checkCache.at < CHECK_CACHE_MS) {
    return { ..._checkCache.data, cached: true };
  }
  const self = await getSelfInfo();
  const gh = (p) =>
    fetch(`https://api.github.com/repos/${GITHUB_REPO}${p}`, {
      headers: { Accept: "application/vnd.github+json", "User-Agent": "__APP_SLUG__-updater" },
      signal: AbortSignal.timeout(8000),
    });
  let release;
  try {
    const r = await gh(`/releases/latest`);
    if (!r.ok) throw new Error(`github ${r.status}`);
    release = await r.json();
  } catch (e) {
    // Don't cache transient failures — let the next call retry.
    return { ok: false, error: String(e.message), current: self, checked_at: new Date().toISOString() };
  }
  const latestTag = release.tag_name || null;

  // Resolve the release tag → commit SHA, then ask GitHub how it relates to the
  // commit we're running. We treat an update as available ONLY when the release
  // commit is strictly AHEAD of ours (status === "ahead"). This is robust against
  // orphaned tags: after the 2026-05-29 PII history rewrite, v0.1.0 points to a
  // commit no longer on main — a plain tag-name compare would falsely offer a
  // downgrade onto a dead commit. "ahead" can't be faked by that.
  let tagSha = null;
  let compareStatus = null;
  try {
    const refR = await gh(`/git/ref/tags/${encodeURIComponent(latestTag)}`);
    if (refR.ok) {
      const ref = await refR.json();
      tagSha = ref.object?.sha || null;
      if (ref.object?.type === "tag" && tagSha) {
        const tagObjR = await gh(`/git/tags/${tagSha}`); // annotated tag → deref to commit
        if (tagObjR.ok) tagSha = (await tagObjR.json()).object?.sha || tagSha;
      }
    }
  } catch {}
  if (self.commit_full && tagSha) {
    try {
      const cmpR = await gh(`/compare/${self.commit_full}...${tagSha}`);
      if (cmpR.ok) compareStatus = (await cmpR.json()).status; // ahead | behind | identical | diverged
    } catch {}
  }
  // Conservative: only "ahead" counts. null/behind/identical/diverged → no update.
  const update_available = compareStatus === "ahead";

  const data = {
    ok: true,
    update_available,
    compare_status: compareStatus,
    current: self,
    latest: {
      tag: latestTag,
      sha: tagSha,
      name: release.name || latestTag,
      published_at: release.published_at || null,
      notes: (release.body || "").slice(0, 4000),
      html_url: release.html_url || null,
      prerelease: !!release.prerelease,
    },
    checked_at: new Date().toISOString(),
  };
  _checkCache = { data, at: now };
  return { ...data, cached: false };
}

export async function readState() {
  try {
    return JSON.parse(await fs.readFile(STATE_PATH, "utf8"));
  } catch {
    return null;
  }
}

const TERMINAL_PHASES = ["success", "rolled_back", "failed", "failed_rollback", "idle"];

// Kick off an apply by spawning the detached worker. Returns immediately; the
// worker drives the rest and reports progress via the update-state file (poll
// readState() / GET /api/update/status).
export async function startApply({ tag } = {}) {
  const st = await readState();
  if (st && st.phase && !TERMINAL_PHASES.includes(st.phase)) {
    return { started: false, reason: "apply_in_progress", state: st };
  }
  const child = spawn(process.execPath, [APPLY_SCRIPT, tag || ""], {
    cwd: REPO_DIR,
    detached: true,
    stdio: "ignore",
    env: { ...process.env },
  });
  child.unref();
  return { started: true, target_tag: tag || "latest", pid: child.pid };
}
