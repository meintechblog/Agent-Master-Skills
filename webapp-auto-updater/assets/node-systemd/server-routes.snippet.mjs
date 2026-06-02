// server-routes.snippet.mjs (node-systemd) — wire into your HTTP server / Fastify.
// Adapt the routing glue to your framework; the 4 handlers + import + getCommitFull
// helper are the substance. /api/health MUST expose self.commit_full (full SHA of
// the running checkout) — the applier confirms the restart landed by matching it.

import { checkForUpdate, startApply, readState, getSelfInfo } from "./lib/updater.mjs";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
const execFileAsync = promisify(execFile);

async function getCommitFull(repoDir = process.cwd()) {
  try { const { stdout } = await execFileAsync("git", ["-C", repoDir, "rev-parse", "HEAD"]); return stdout.trim(); }
  catch { return null; }
}

// --- Fastify example ---
// fastify.get("/api/update/check",  async (req)      => checkForUpdate(req.query.refresh === "1"));
// fastify.get("/api/update/status", async ()         => ({ self: await getSelfInfo(), state: await readState() }));
// fastify.post("/api/update/apply", async (req)      => startApply(req.body || {}));   // body: { tag?, sha? } → defaults to latest
// fastify.get("/api/health",        async ()         => ({ ok: true, self: { commit_full: await getCommitFull() } }));

// --- raw node:http example ---
// GET  /api/update/check   → checkForUpdate(refresh?)
// GET  /api/update/status  → { self, state }   (poll during an apply)
// POST /api/update/apply   → startApply({tag?,sha?})  (writes trigger; root oneshot does the work)
// GET  /api/health         → { ok, self: { commit_full } }   (commit_full REQUIRED)
//
// Apply is manual-only by design: the badge shows "available", the POST only fires
// on click. For an OPTIONAL nightly auto-apply, add a scheduler that calls
// startApply() at a fixed hour when checkForUpdate().update_available — gate it
// behind a config flag (default off for shared/multi-box services).
export { getCommitFull };
