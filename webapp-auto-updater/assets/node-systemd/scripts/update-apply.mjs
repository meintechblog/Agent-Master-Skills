#!/usr/bin/env node
// node-systemd mode — PRIVILEGED applier. Run as ROOT by the systemd oneshot
// __APP_SLUG__-updater.service, which is activated by __APP_SLUG__-updater.path
// when the app writes /etc/__APP_SLUG__/update-trigger.json.
//
// Deliberately SELF-CONTAINED (no imports from this repo): the repo's own files
// are swapped under this process during checkout, so it must not depend on them.
//
// Flow (in-place git checkout, root, systemctl restart):
//   read+validate trigger (nonce dedup) → fetch tags → resolve target SHA →
//   verify SHA is an ancestor of origin/main → record rollback SHA + write
//   PENDING marker → git checkout -B main <sha> → preflight (node --check) →
//   npm ci (if deps) → systemctl restart → poll /api/health for target
//   commit_full (90s). On any failure after checkout: revert to rollback SHA +
//   restart. Clear PENDING marker only on confirmed-healthy.
//
// Exit codes: 0 ok · 1 preflight/abort (no checkout or safely reverted) ·
//             2 healthcheck failed but rollback succeeded · 3 rollback FAILED.
//
// Placeholders: __APP_SLUG__ __GITHUB_REPO__ __APP_PORT__ __ENV_PREFIX__

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import fs from "node:fs/promises";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

const execFileP = promisify(execFile);

const REPO_DIR     = process.env.__ENV_PREFIX___INSTALL_DIR || "/opt/__APP_SLUG__";
const GITHUB_REPO  = process.env.__ENV_PREFIX___GITHUB_REPO || "__GITHUB_REPO__";
const SERVICE      = process.env.__ENV_PREFIX___SERVICE     || "__APP_SLUG__.service";
const TRIGGER_PATH = process.env.__ENV_PREFIX___TRIGGER     || "/etc/__APP_SLUG__/update-trigger.json";
const STATE_PATH   = process.env.__ENV_PREFIX___UPDATE_STATE|| "/var/lib/__APP_SLUG__/update-state.json";
const PENDING_PATH = process.env.__ENV_PREFIX___PENDING     || "/var/lib/__APP_SLUG__/update-pending.json";
const NONCE_PATH   = process.env.__ENV_PREFIX___NONCE       || "/var/lib/__APP_SLUG__/update-nonce.json";
const PORT         = parseInt(process.env.__ENV_PREFIX___PORT || "__APP_PORT__", 10);
// Health endpoint must return JSON exposing the running full SHA. Default
// /api/health.self.commit_full; override URL + JSON path if your app already has
// one (e.g. /api/version with .sha) via the env vars below.
const HEALTH_URL   = process.env.__ENV_PREFIX___HEALTH_URL || `http://127.0.0.1:${PORT}/api/health`;
const HEALTH_SHA_PATH = (process.env.__ENV_PREFIX___HEALTH_SHA_PATH || "self.commit_full").split(".");
const HEALTH_TIMEOUT_MS = 90_000;

let state = { phase: "starting", started_at: new Date().toISOString(), log: [] };

async function git(args) {
  const { stdout } = await execFileP("git", ["-c", "safe.directory=*", "-C", REPO_DIR, ...args], { timeout: 120_000 });
  return stdout.trim();
}
async function setPhase(phase, msg) {
  state.phase = phase;
  if (msg) state.log.push(`${new Date().toISOString()} ${msg}`);
  state.updated_at = new Date().toISOString();
  try { await fs.mkdir(path.dirname(STATE_PATH), { recursive: true }); await fs.writeFile(STATE_PATH, JSON.stringify(state, null, 2)); } catch {}
}
async function restart() {
  await execFileP("systemctl", ["restart", SERVICE], { timeout: 60_000 });
}
function depsCount() {
  try { return Object.keys(JSON.parse(readFileSync(path.join(REPO_DIR, "package.json"), "utf8")).dependencies || {}).length; }
  catch { return 0; }
}
async function npmCiIfNeeded() {
  if (depsCount() === 0) return;
  await setPhase("installing", "npm ci (deps detected)");
  // Prefer reproducible ci; fall back to install if no lockfile.
  const hasLock = existsSync(path.join(REPO_DIR, "package-lock.json"));
  await execFileP("npm", [hasLock ? "ci" : "install", "--omit=dev", "--no-audit", "--no-fund"], { cwd: REPO_DIR, timeout: 600_000 });
}
async function waitForHealth(wantSha) {
  const deadline = Date.now() + HEALTH_TIMEOUT_MS;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 3000));
    try {
      const r = await fetch(HEALTH_URL, { signal: AbortSignal.timeout(3000) });
      if (r.ok) { const j = await r.json(); if (HEALTH_SHA_PATH.reduce((o, k) => o?.[k], j) === wantSha) return true; }
    } catch {}
  }
  return false;
}

async function readTrigger() {
  const raw = JSON.parse(await fs.readFile(TRIGGER_PATH, "utf8"));
  if (!raw || typeof raw !== "object") throw new Error("trigger not an object");
  if (!raw.nonce || typeof raw.nonce !== "string") throw new Error("trigger missing nonce");
  if (raw.target_sha && !/^[0-9a-f]{7,40}$/i.test(raw.target_sha)) throw new Error("bad target_sha");
  // Nonce dedup: refuse to replay the same trigger.
  try {
    const seen = JSON.parse(await fs.readFile(NONCE_PATH, "utf8"));
    if (seen?.nonce === raw.nonce) throw new Error(`nonce_already_processed:${raw.nonce}`);
  } catch (e) { if (String(e.message).startsWith("nonce_already_processed")) throw e; }
  await fs.mkdir(path.dirname(NONCE_PATH), { recursive: true }).catch(() => {});
  await fs.writeFile(NONCE_PATH, JSON.stringify({ nonce: raw.nonce, at: new Date().toISOString() }));
  return raw;
}

async function resolveTargetSha(trigger) {
  await setPhase("fetching", "git fetch --tags origin");
  await git(["fetch", "--tags", "--prune", "origin", "main"]);
  if (trigger.target_sha) return await git(["rev-parse", `${trigger.target_sha}^{commit}`]);
  let tag = trigger.target_tag;
  if (!tag) {
    const r = await fetch(`https://api.github.com/repos/${GITHUB_REPO}/releases/latest`, {
      headers: { Accept: "application/vnd.github+json", "User-Agent": "__APP_SLUG__-updater" },
      signal: AbortSignal.timeout(8000),
    });
    if (!r.ok) throw new Error(`github releases ${r.status}`);
    tag = (await r.json()).tag_name;
    if (!tag) throw new Error("no tag_name in latest release");
  }
  return await git(["rev-parse", `${tag}^{commit}`]);
}

async function main() {
  let trigger;
  try { trigger = await readTrigger(); }
  catch (e) { await setPhase("idle", `no/invalid/duplicate trigger: ${e.message}`); return 0; }

  await setPhase("starting", `update requested → ${trigger.target_tag || trigger.target_sha || "latest"}`);
  const rollbackSha = await git(["rev-parse", "HEAD"]);
  state.from_commit = rollbackSha;

  let targetSha;
  try { targetSha = await resolveTargetSha(trigger); }
  catch (e) { await setPhase("failed", `resolve failed: ${e.message}`); return 1; }
  state.target_commit = targetSha;

  if (targetSha === rollbackSha) { await setPhase("success", "already at target"); return 0; }

  // Safety: target MUST be an ancestor of origin/main (release tags always are).
  try { await git(["merge-base", "--is-ancestor", targetSha, "origin/main"]); }
  catch { await setPhase("failed", `target ${targetSha.slice(0,7)} not an ancestor of origin/main — refusing`); return 1; }

  // PENDING marker first, so the recovery unit can revert if we die mid-flight.
  try { await fs.mkdir(path.dirname(PENDING_PATH), { recursive: true }); await fs.writeFile(PENDING_PATH, JSON.stringify({ from_sha: rollbackSha, target_sha: targetSha, at: new Date().toISOString() })); } catch {}

  try { await setPhase("checking_out", `git checkout -B main ${targetSha.slice(0,7)}`); await git(["checkout", "-f", "-B", "main", targetSha]); }
  catch (e) { await git(["checkout", "-f", "-B", "main", rollbackSha]).catch(() => {}); await fs.rm(PENDING_PATH).catch(() => {}); await setPhase("failed", `checkout failed: ${e.message}`); return 1; }

  // Preflight on the NEW code: node --check on entrypoints + lib/*.mjs.
  await setPhase("preflight", "node --check entrypoints + lib/*.mjs");
  const preflight = [];
  for (const cand of ["server.mjs", "server.js", "index.mjs", "index.js", "app.mjs", "app.js"]) {
    if (existsSync(path.join(REPO_DIR, cand))) preflight.push(cand);
  }
  try { for (const f of await fs.readdir(path.join(REPO_DIR, "lib"))) if (f.endsWith(".mjs") || f.endsWith(".js")) preflight.push(path.join("lib", f)); } catch {}
  for (const f of preflight) {
    try { await execFileP(process.execPath, ["--check", path.join(REPO_DIR, f)], { timeout: 20_000 }); }
    catch (e) { await git(["checkout", "-f", "-B", "main", rollbackSha]).catch(() => {}); await fs.rm(PENDING_PATH).catch(() => {}); await setPhase("failed", `preflight failed on ${f}: ${String(e.stderr || e.message).slice(0,300)}`); return 1; }
  }

  try { await npmCiIfNeeded(); }
  catch (e) { await git(["checkout", "-f", "-B", "main", rollbackSha]).catch(() => {}); await fs.rm(PENDING_PATH).catch(() => {}); await setPhase("failed", `npm ci failed: ${e.message}`); return 1; }

  await setPhase("restarting", `systemctl restart ${SERVICE}`);
  try { await restart(); } catch (e) { await setPhase("restarting", `restart returned ${e.message} (continuing to health-poll)`); }

  if (await waitForHealth(targetSha)) {
    await fs.rm(PENDING_PATH).catch(() => {});
    await setPhase("success", `live on ${targetSha.slice(0,7)}`);
    return 0;
  }

  // Rollback.
  await setPhase("rolling_back", `health not green — reverting to ${rollbackSha.slice(0,7)}`);
  try {
    await git(["checkout", "-f", "-B", "main", rollbackSha]);
    await npmCiIfNeeded();
    await restart().catch(() => {});
  } catch (e) { await setPhase("failed_rollback", `ROLLBACK FAILED: ${e.message} — manual SSH needed`); return 3; }

  if (await waitForHealth(rollbackSha)) { await fs.rm(PENDING_PATH).catch(() => {}); await setPhase("rolled_back", "reverted, healthy again"); return 2; }
  await setPhase("failed_rollback", "rollback restart not green — manual SSH needed"); return 3;
}

main().then((rc) => process.exit(rc)).catch(async (e) => { await setPhase("failed", `unexpected: ${String(e.message)}`); process.exit(1); });
