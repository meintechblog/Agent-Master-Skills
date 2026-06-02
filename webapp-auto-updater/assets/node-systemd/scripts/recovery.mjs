#!/usr/bin/env node
// node-systemd mode — boot recovery. Run as ROOT by __APP_SLUG__-recovery.service
// (Before=__APP_SLUG__.service). If a PENDING marker survived a crash mid-update
// (box rebooted between checkout and confirmed-healthy), revert the checkout to
// the recorded from_sha so the app boots on the last known-good commit.
//
// Self-contained. Placeholders: __APP_SLUG__ __ENV_PREFIX__

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import fs from "node:fs/promises";

const execFileP = promisify(execFile);
const REPO_DIR     = process.env.__ENV_PREFIX___INSTALL_DIR || "/opt/__APP_SLUG__";
const PENDING_PATH = process.env.__ENV_PREFIX___PENDING     || "/var/lib/__APP_SLUG__/update-pending.json";

async function main() {
  let pending;
  try { pending = JSON.parse(await fs.readFile(PENDING_PATH, "utf8")); }
  catch { return; } // no pending marker → nothing to recover

  if (!pending?.from_sha) { await fs.rm(PENDING_PATH).catch(() => {}); return; }
  try {
    await execFileP("git", ["-c", "safe.directory=*", "-C", REPO_DIR, "checkout", "-f", "-B", "main", pending.from_sha], { timeout: 120_000 });
    console.log(`[recovery] reverted to ${pending.from_sha.slice(0, 7)} after interrupted update`);
  } catch (e) {
    console.error(`[recovery] revert failed: ${e.message}`);
  } finally {
    await fs.rm(PENDING_PATH).catch(() => {});
  }
}
main();
