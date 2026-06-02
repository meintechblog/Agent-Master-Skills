# Next.js / Node-Stack — Adaption

Das **Sicherheits- und Release-Modell ist identisch** zum Python-Stack (siehe `architecture.md` +
`github-release-conventions.md`): GitHub-Release-getrieben, semver-Tags am main-HEAD, git-Clone-Deploy,
unprivilegierte App schreibt nur einen Trigger, privilegierter Prozess macht blue-green + Healthcheck +
Rollback. Nur die **Install-/Restart-Primitive** unterscheiden sich. Darum wird der JS-Pfad als
Referenz-Scaffolding dokumentiert statt blind emittiert.

## Was 1:1 übernehmbar ist (Logik, sprachunabhängig)
- Release-Detection (`/releases/latest` + ETag + Prerelease-Filter) → in TS portieren oder per
  `child_process` ein kleines Helper-Script callen.
- Versions-Quelle: `package.json` `version` (statt pyproject) — muss im Lockstep mit dem Release-Tag bumpen.
- Trigger-Datei + Nonce-Dedup + „SHA ist Ancestor von origin/main"-Gate: identisch.
- Zwei-Prozess-Trennung + systemd `.path`→`oneshot` (oder ein root-pm2-Hook).

## Was ersetzt werden muss (Build/Restart)
| Python-Primitive | Node-Äquivalent |
|---|---|
| `python -m venv` + `pip install` | `npm ci` (bzw. `pnpm i --frozen-lockfile`) |
| byte-compile / smoke-import Pre-Flight | `npm run build` (Build-Fehler = Abbruch VOR Flip) |
| `systemctl restart <slug>.service` | `systemctl restart <slug>.service` **oder** `pm2 reload <app>` |
| Healthcheck `GET /api/health` | identisch — Next.js `app/api/health/route.ts` ergänzen |

## Empfohlener Ablauf
1. blue-green-Release-Dir auschecken (`git fetch` + `git checkout <sha>` in `/opt/<slug>-releases/<sha>`).
2. `npm ci && npm run build` in der neuen Dir. **Build scheitert → kein Symlink-Flip, sauberer Abbruch.**
3. Symlink `/opt/<slug>` → neue Dir atomar flippen; `PENDING`-Marker.
4. Restart (systemd oder `pm2 reload`); 60s-Healthcheck.
5. grün → Marker clearen; rot → Symlink zurück + Restart (Rollback).

## Process-Manager-Wahl
- **systemd** (wie Python-Stack): identische Unit-Struktur, `ExecStart=node server.js` bzw.
  `ExecStart=npm run start`. Empfohlen für Konsistenz im LXC-Setup.
- **pm2:** wenn schon im Einsatz. Dann läuft der root-Installer `pm2 reload` statt `systemctl restart`;
  Healthcheck/Rollback unverändert.

`assets/nextjs/` enthält Platzhalter-Stubs als Startpunkt. Bei echtem Bedarf: hier ausbauen und die
Python-Logik portieren — die schwierigen Teile (SHA-Gate, blue-green, Rollback, cgroup-Falle) sind in
`architecture.md` beschrieben und gelten 1:1.
