// node-systemd mode — detection + trigger (runs as the NON-root app service user).
//
// Architecture (gold standard, mirrors the python-systemd mode):
//   - This module runs inside the web app (unprivileged). It NEVER touches /opt
//     or restarts services. It only DETECTS updates and WRITES A TRIGGER FILE.
//   - A separate root systemd oneshot (scripts/update-apply.mjs, activated by a
//     .path unit watching the trigger file) does the privileged work.
//   - Release-driven detection via the GitHub compare-API ("ahead"), robust
//     against orphaned tags (history rewrite) — only a release commit strictly
//     ahead of HEAD counts as an update.
//
// Placeholders (substituted by install.sh): __GITHUB_REPO__ __APP_SLUG__
//   __APP_PORT__ __ENV_PREFIX__

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import fs from "node:fs/promises";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const execFileP = promisify(execFile);
const __dirname = path.dirname(fileURLToPath(import.meta.url));

export const REPO_DIR = path.resolve(__dirname, "..");
export const GITHUB_REPO = process.env.__ENV_PREFIX___GITHUB_REPO || "__GITHUB_REPO__";
export const TRIGGER_PATH = process.env.__ENV_PREFIX___TRIGGER || "/etc/__APP_SLUG__/update-trigger.json";
export const STATE_PATH = process.env.__ENV_PREFIX___UPDATE_STATE || "/var/lib/__APP_SLUG__/update-state.json";

const CHECK_CACHE_MS = 10 * 60 * 1000;
let _checkCache = { data: null, at: 0 };
let _selfCache = null;

async function git(args) {
  const { stdout } = await execFileP("git", ["-C", REPO_DIR, ...args], { timeout: 60_000 });
  return stdout.trim();
}

export function getLocalVersion() {
  try {
    const pkg = JSON.parse(readFileSync(path.join(REPO_DIR, "package.json"), "utf8"));
    return pkg.version || null;
  } catch { return null; }
}

async function getLocalCommit() {
  try {
    const full = await git(["rev-parse", "HEAD"]);
    return { full, short: full.slice(0, 7) };
  } catch { return { full: null, short: null }; }
}

// version + commit + nearest tag of the running checkout. Cached in-process; the
// process restarts on a successful update so a stale cache can't outlive a real
// version change.
export async function getSelfInfo() {
  if (_selfCache) return _selfCache;
  const commit = await getLocalCommit();
  let describe = null;
  try { describe = await git(["describe", "--tags", "--always"]); } catch {}
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
  const token = process.env.GITHUB_TOKEN;
  const gh = (p) =>
    fetch(`https://api.github.com/repos/${GITHUB_REPO}${p}`, {
      headers: {
        Accept: "application/vnd.github+json",
        "User-Agent": "__APP_SLUG__-updater",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
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

  // Resolve release tag → commit SHA, then ask GitHub how it relates to our HEAD.
  // Update available ONLY when the release commit is strictly AHEAD (robust
  // against orphaned tags: a dead tag can't be "ahead").
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
      if (cmpR.ok) compareStatus = (await cmpR.json()).status; // ahead|behind|identical|diverged
    } catch {}
  }
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
  try { return JSON.parse(await fs.readFile(STATE_PATH, "utf8")); }
  catch { return null; }
}

const TERMINAL_PHASES = ["success", "rolled_back", "failed", "failed_rollback", "idle"];

// Kick off an apply by WRITING A TRIGGER FILE (atomic). The root .path unit
// notices the modified trigger and activates the privileged oneshot applier.
// Returns immediately; poll readState() / GET /api/update/status for progress.
export async function startApply({ tag, sha } = {}) {
  const st = await readState();
  if (st && st.phase && !TERMINAL_PHASES.includes(st.phase)) {
    return { started: false, reason: "apply_in_progress", state: st };
  }
  if (!tag && !sha) {
    // Default to the latest detected release.
    const chk = await checkForUpdate(true);
    if (!chk.update_available) return { started: false, reason: "no_update_available" };
    tag = chk.latest.tag;
    sha = chk.latest.sha;
  }
  const payload = {
    target_tag: tag || null,
    target_sha: sha || null,
    nonce: `${Date.now().toString(36)}-${Math.floor(performance.now())}`,
    requested_at: new Date().toISOString(),
  };
  // Atomic write: tmp + rename so the .path unit never sees a partial trigger.
  const tmp = `${TRIGGER_PATH}.tmp`;
  await fs.mkdir(path.dirname(TRIGGER_PATH), { recursive: true }).catch(() => {});
  await fs.writeFile(tmp, JSON.stringify(payload, null, 2));
  await fs.rename(tmp, TRIGGER_PATH);
  return { started: true, target_tag: tag || "latest", target_sha: sha || null };
}
