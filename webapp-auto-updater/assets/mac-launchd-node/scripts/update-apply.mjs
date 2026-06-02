#!/usr/bin/env node
// Detached update-worker für die Node-Webapp.
//
// Wird von lib/updater.mjs (startApply) als losgelöster, unref'd Prozess
// gestartet — damit es den `launchctl kickstart -k` überlebt, der die App-
// Service mitten im Update neu startet (sonst würde der Worker im sterbenden
// Service-Prozessbaum mitgekillt).
//
// Ablauf (in-place git-Checkout, kein root, launchd-Restart):
//   fetch tags → Ziel-Tag→SHA auflösen → prüfen dass SHA auf origin/main liegt
//   → Rollback-SHA merken → Checkout (git checkout -B main <sha>) → Preflight
//   (node --check auf server.mjs + lib/*.mjs) → npm install NUR wenn das neue
//   package.json Dependencies hat → kickstart → 90s /api/health pollen bis die
//   neue commit_full erscheint. Schlägt irgendwas fehl: zurück auf die Rollback-
//   SHA + erneut kickstart.
//
// Bewusst self-contained (keine Imports aus diesem Repo): die eigenen lib-Files
// werden während des Checkouts unter dem Worker ausgetauscht.

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import fs from "node:fs/promises";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const execFileP = promisify(execFile);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_DIR = path.resolve(__dirname, "..");
const GITHUB_REPO = "__GITHUB_REPO__";
const LAUNCHD_LABEL = "__LAUNCHD_LABEL__";
const STATE_PATH = path.join(REPO_DIR, "data", "update-state.json");
const PORT = parseInt(process.env.__ENV_PREFIX___PORT || "__APP_PORT__", 10);
const HEALTH_URL = `http://127.0.0.1:${PORT}/api/health`;
const HEALTH_TIMEOUT_MS = 90_000;

const targetArg = (process.argv[2] || "").trim();

let state = {
  phase: "starting",
  target_tag: targetArg || "latest",
  started_at: new Date().toISOString(),
  log: [],
};

async function git(args) {
  const { stdout } = await execFileP("git", args, { cwd: REPO_DIR, timeout: 120_000 });
  return stdout.trim();
}

async function setPhase(phase, msg) {
  state.phase = phase;
  if (msg) state.log.push(`${new Date().toISOString()} ${msg}`);
  state.updated_at = new Date().toISOString();
  try {
    await fs.writeFile(STATE_PATH, JSON.stringify(state, null, 2));
  } catch {}
}

async function kickstart() {
  await execFileP("launchctl", ["kickstart", "-k", `gui/${process.getuid()}/${LAUNCHD_LABEL}`], {
    timeout: 30_000,
  });
}

function depsCount() {
  try {
    const pkg = JSON.parse(readFileSync(path.join(REPO_DIR, "package.json"), "utf8"));
    return Object.keys(pkg.dependencies || {}).length;
  } catch {
    return 0;
  }
}

async function npmInstallIfNeeded() {
  if (depsCount() === 0) return; // App ist pure Node-stdlib — kein install nötig.
  await setPhase("installing", "npm install (deps detected)");
  await execFileP("npm", ["install", "--no-audit", "--no-fund"], {
    cwd: REPO_DIR,
    timeout: 300_000,
  });
}

// Pollt /api/health bis .self.commit_full == wantSha (oder Timeout).
async function waitForHealth(wantSha) {
  const deadline = Date.now() + HEALTH_TIMEOUT_MS;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 3000));
    try {
      const r = await fetch(HEALTH_URL, { signal: AbortSignal.timeout(3000) });
      if (r.ok) {
        const j = await r.json();
        if (j?.self?.commit_full === wantSha) return true;
      }
    } catch {}
  }
  return false;
}

async function resolveTargetSha() {
  await setPhase("fetching", "git fetch --tags");
  await git(["fetch", "--tags", "--prune", "origin"]);
  let tag = targetArg;
  if (!tag) {
    // Latest release tag from GitHub.
    const r = await fetch(`https://api.github.com/repos/${GITHUB_REPO}/releases/latest`, {
      headers: { Accept: "application/vnd.github+json", "User-Agent": "__APP_SLUG__-updater" },
      signal: AbortSignal.timeout(8000),
    });
    if (!r.ok) throw new Error(`github releases ${r.status}`);
    const rel = await r.json();
    tag = rel.tag_name;
    if (!tag) throw new Error("no tag_name in latest release");
  }
  state.target_tag = tag;
  const sha = await git(["rev-parse", `${tag}^{commit}`]);
  return { tag, sha };
}

async function main() {
  await setPhase("starting", `update requested → ${state.target_tag}`);
  const rollbackSha = await git(["rev-parse", "HEAD"]);
  state.from_commit = rollbackSha;

  let target;
  try {
    target = await resolveTargetSha();
  } catch (e) {
    return setPhase("failed", `resolve failed: ${e.message}`);
  }
  state.target_commit = target.sha;

  if (target.sha === rollbackSha) {
    return setPhase("success", "already at target — nothing to do");
  }

  // Safety: das Ziel MUSS auf origin/main liegen (Release-Tags sind das immer).
  try {
    await git(["merge-base", "--is-ancestor", target.sha, "origin/main"]);
  } catch {
    return setPhase("failed", `target ${target.tag} (${target.sha.slice(0, 7)}) is not an ancestor of origin/main — refusing`);
  }

  // Checkout in-place auf main → target.
  try {
    await setPhase("checking_out", `git checkout -B main ${target.sha.slice(0, 7)} (${target.tag})`);
    await git(["checkout", "-B", "main", target.sha]);
  } catch (e) {
    await git(["checkout", "-B", "main", rollbackSha]).catch(() => {});
    return setPhase("failed", `checkout failed: ${e.message}`);
  }

  // Preflight auf dem NEUEN Code — node --check für server.mjs + lib/*.mjs.
  await setPhase("preflight", "node --check server.mjs + lib/*.mjs");
  const preflightFiles = ["server.mjs"];
  try {
    for (const f of await fs.readdir(path.join(REPO_DIR, "lib"))) {
      if (f.endsWith(".mjs")) preflightFiles.push(path.join("lib", f));
    }
  } catch {}
  for (const f of preflightFiles) {
    const abs = path.join(REPO_DIR, f);
    if (!existsSync(abs)) continue;
    try {
      await execFileP(process.execPath, ["--check", abs], { timeout: 20_000 });
    } catch (e) {
      await git(["checkout", "-B", "main", rollbackSha]).catch(() => {});
      return setPhase("failed", `preflight failed on ${f}: ${String(e.stderr || e.message).slice(0, 300)}`);
    }
  }

  try {
    await npmInstallIfNeeded();
  } catch (e) {
    await git(["checkout", "-B", "main", rollbackSha]).catch(() => {});
    return setPhase("failed", `npm install failed: ${e.message}`);
  }

  // Restart + Healthcheck.
  await setPhase("restarting", `launchctl kickstart -k ${LAUNCHD_LABEL}`);
  try {
    await kickstart();
  } catch (e) {
    await setPhase("restarting", `kickstart returned: ${e.message} (continuing to health-poll)`);
  }

  if (await waitForHealth(target.sha)) {
    return setPhase("success", `live on ${target.tag} (${target.sha.slice(0, 7)})`);
  }

  // Rollback.
  await setPhase("rolling_back", `health did not come green on ${target.tag} — reverting to ${rollbackSha.slice(0, 7)}`);
  try {
    await git(["checkout", "-B", "main", rollbackSha]);
    await npmInstallIfNeeded();
    await kickstart().catch(() => {});
  } catch (e) {
    return setPhase("failed_rollback", `ROLLBACK FAILED: ${e.message} — manual SSH needed`);
  }
  if (await waitForHealth(rollbackSha)) {
    return setPhase("rolled_back", "reverted to previous version, app healthy again");
  }
  return setPhase("failed_rollback", "rollback restart did not come green — manual SSH needed");
}

main().catch(async (e) => {
  await setPhase("failed", `unexpected: ${String(e.message)}`);
});
