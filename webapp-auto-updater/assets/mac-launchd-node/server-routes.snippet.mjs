// server-routes.snippet.mjs — wire these into your Node HTTP server (mac-launchd-node mode).
// Adapt to your router (this is the raw-http shape from the reference webapp). Positions in
// your real server differ; copy the 3 handlers + the import + the getCommitFull helper.

import { checkForUpdate, startApply } from "./lib/updater.mjs";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
const execFileAsync = promisify(execFile);

// Full SHA of the running checkout — the health endpoint MUST expose this as
// `self.commit_full` so the detached applier can confirm the restart landed.
async function getCommitFull() {
  try {
    const { stdout } = await execFileAsync("git", ["rev-parse", "HEAD"], { cwd: process.cwd() });
    return stdout.trim();
  } catch { return null; }
}

// --- routes (drop into your request handler) ---

// GET /api/update/check  → { available, running, latest, compareStatus, aheadBy, tag, htmlUrl }
if (url.pathname === "/api/update/check" && req.method === "GET") {
  const upd = await checkForUpdate();
  return json(res, 200, upd);
}

// POST /api/update/apply  body: { target }  → { started, pid, target }
// Respond BEFORE the kickstart lands (the detached worker sleeps briefly first).
if (url.pathname === "/api/update/apply" && req.method === "POST") {
  const body = await readBody(req);
  if (!body?.target) return json(res, 400, { error: "target_required" });
  const r = await startApply(body.target);
  return json(res, 200, r);
}

// GET /api/health  → { ok, self: { commit_full } }   (commit_full is REQUIRED)
if (url.pathname === "/api/health" && req.method === "GET") {
  return json(res, 200, { ok: true, self: { commit_full: await getCommitFull() } });
}
