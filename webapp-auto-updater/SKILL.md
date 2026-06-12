---
name: webapp-auto-updater
description: >-
  Baut eine sichere One-Click- + nächtliche Auto-Update-Funktion (GitHub-Release-getrieben,
  blue-green, privilegierter Root-Installer mit Healthcheck + Rollback) in eine selbst-gehostete
  Web-App ein. Code-Generator: kopiert templatete Updater-Assets ins Ziel-Repo und ersetzt die
  Projekt-Platzhalter. Erstklassig für den Python/aiohttp + systemd + git-blue-green Standard-Stack
  (self-hosted boxes on LXC); Next.js/Node als dokumentiertes Referenz-Scaffolding.
  Nutze diesen Skill, wenn ein Agent „Auto-Update / One-Click-Update / Software-Update-Button /
  nightly self-update" in seine Web-App einbauen will.
---

# webapp-auto-updater

Verpackt eine produktiv erprobte Update-Maschinerie als wiederverwendbaren Generator.
Vorbild-Stil: `webapp-chat-bridge` — Code-Generator ins Ziel-Repo, idempotent, re-runnable.

## Was es liefert

- **Versions-Erkennung** (`updater/version.py`): laufende Version via `importlib.metadata`, `Version`-NamedTuple (semver-Vergleich), `resolve_remote_main_sha()` löst `origin/main` HEAD als konkretes Install-Ziel auf.
- **Release-Polling** (`updater/github_client.py`): `/releases/latest` mit ETag-Cache, Prerelease-Filter, „never raises".
- **Scheduler + nightly auto-update** (`updater/scheduler.py`, `updater/auto_update.py`): periodischer Check + 04:00-(TZ)-Auto-Install wenn `enabled` + `auto_install` + Update verfügbar.
- **Config** (`updater/config.py`): `UpdateConfig` (github_repo, check_interval_hours, auto_install=True, enabled=True), YAML read-modify-write unter `update:`-Key, PATCH-Allowlist-Validierung.
- **Trigger + Sicherheit** (`updater/trigger.py`, `updater/security.py`): nicht-privilegierte Web-App schreibt einen validierten Install-Trigger; nur SHAs die **Ancestor von origin/main** sind werden akzeptiert (Nonce-Dedup, Schema-Validierung).
- **Privilegierter Root-Installer** (`updater_root/`): systemd `oneshot` als root. blue-green: frischer Release-Checkout → venv + pip → byte-compile/smoke-import → atomarer Symlink-Flip → `systemctl restart` → 60s-Healthcheck → Rollback bei Fehler. Exit-Codes 0/1/2/3. Recovery-Unit flippt bei `PENDING`-Marker beim Boot zurück.
- **HTTP-Routen** (`webapp/routes_updater.py`): `/api/version`, `/api/update/{available,start,check,status,config,rollback}`.
- **Frontend** (`static/software_page.js`): Versions-Card, Install-Button, Release-Notes, Auto-Update-Toggle.
- **systemd-Units**: `<slug>.service`, `<slug>-updater.{service,path}`, `<slug>-recovery.service`.

## Sicherheitsmodell (warum 2 Prozesse)

Die Web-App läuft unprivilegiert und darf **nicht** selbst nach `/opt` schreiben oder Dienste neu starten.
Sie schreibt nur eine Trigger-Datei. Eine getrennte **root**-`oneshot`-Unit (per `.path`-Unit auf die
Trigger-Datei getriggert) macht die privilegierte Arbeit. **Kritisch:** die Updater-Unit darf **kein**
`Requires=/BindsTo=/PartOf=` der Haupt-Unit haben — sonst killt systemd den Updater mit, wenn er den
Hauptdienst neu startet (cgroup). Details: `references/architecture.md`.

## Verwendung

```bash
~/.claude/skills/webapp-auto-updater/scripts/install.sh \
  --repo /pfad/zum/ziel-repo \
  --github-repo owner/repo \
  [--app-slug my-app] [--pkg-name my_app] [--service-user my-app] \
  [--app-port 8080] [--tz Europe/Berlin] [--dry-run]
```

Stack wird auto-erkannt (`pyproject.toml`/`src/*/__init__.py` → python-systemd; `package.json` → nextjs).
Erst `--dry-run` zum Sichten, dann echt. Danach den ausgegebenen **MANUAL WIRING**-Block abarbeiten
(app.py/`__main__.py`-Wiring + pyproject-Version-Check) — diese Stellen sind app-spezifisch und werden
bewusst NICHT blind überschrieben.

## Voraussetzungen & Konventionen (PFLICHTLEKTÜRE)

Der häufigste Grund, warum „der Updater zeigt kein Update": die Release-Konventionen wurden nicht
eingehalten. **Vor dem ersten Release `references/github-release-conventions.md` lesen.** Kurzfassung:

- **Echtes GitHub-Release** nötig (nicht nur ein Tag) — Detection via `/releases/latest`, Prereleases gefiltert.
- **Semver-Tags** `vMAJOR.MINOR.PATCH`; Tag **und** `pyproject.toml [project].version` im **Lockstep** bumpen.
- **Release am main-HEAD schneiden:** `gh release create vX.Y.Z --target main`. Der Updater installiert `origin/main` HEAD (== Release-Commit) und akzeptiert nur SHAs die Ancestor von `origin/main` sind. Also: erst auf main mergen+pushen, dann taggen.
- **Ziel-Box = git-Clone** (blue-green via install.sh), nicht rsync — sonst kann der git-basierte Updater nicht auschecken. `update.enabled=true` + `update.github_repo=owner/repo` in der config.yaml.
- **Betreiber-Cheatsheet:** Feature mergen → Version bumpen → push → `gh release create vX.Y.Z --target main` → fertig, alle Instanzen ziehen beim nächsten Check / um 04:00 nach.
- **🆕 Privates Repo / origin-Kette (Fix 2026-06-12):** `git clone --shared` vom Vorgänger-Release
  setzt `origin` auf den LOKALEN Pfad → der Ancestor-Security-Check (`merge-base --is-ancestor
  origin/main`) prüft beim nächsten Update einen eingefrorenen Stand → jedes Update stirbt mit
  `sha_not_on_main`. **Strukturell gefixt im Template** (runner.py Schritt 5b propagiert die echte
  Remote-URL des laufenden Release auf jeden neuen; git_ops `git_get_origin_url`/`git_set_origin_url`).
  **Bestands-Deployments brauchen den EINMALIGEN Hand-Fix** (danach trägt die Propagation):
  (1) `git -C /opt/<app>/releases/current remote set-url origin https://github.com/<owner>/<repo>.git`;
  (2) bei privatem Repo Token ROOT-ONLY: `git config credential.helper store` + Token nach
  `/root/.git-credentials` (chmod 600) — NIE in `.git/config` (user-lesbar); (3) `--shared`-Alternates:
  alte Release-Dirs nicht löschen, solange Nachfolger deren Object-Store referenzieren.

## Stack-Scope

- **python-systemd** — voll unterstützt, 1:1 generalisiert aus einem Produktions-Stack (LXC/systemd/git-blue-green).
- **node-systemd** — voll unterstützt, turn-key. Node-Web-App (bare http/Fastify/Express) auf systemd/LXC. Gold-Standard wie python-systemd, an Node adaptiert: release-driven compare-API-Detection, 2-Prozess-Privileg-Trennung (root-oneshot via `.path`-Trigger), in-place `git checkout` + preflight + `npm ci` + Healthcheck + Rollback + Recovery-Unit. Aufruf: `--stack node-systemd`. ⚠ Rollback-/Recovery-Leg vor Produktiv-Vertrauen per Fail-Inject testen. Details: `assets/node-systemd/README.md`.
- **mac-launchd-node** — Node-App auf macOS via user-`launchd` (kein root, kein systemd, in-place `git checkout -B main` + `launchctl kickstart`, detached self-contained Apply-Worker, compare-API-Detection robust gegen verwaiste Tags). Aus einer Produktions-Referenz-Implementierung templatet. Aufruf: `--stack mac-launchd-node --launchd-label com.you.app`. ⚠ **Rollback-on-failure-Leg ist UNTESTED** — vor Produktiv-Vertrauen einen Fail-Inject-Test fahren. Details: `assets/mac-launchd-node/README.md`.
- **nextjs/node (systemd/pm2)** — `references/nextjs-notes.md` beschreibt den abweichenden Deploy-/Restart-Pfad (pm2 oder systemd + `npm ci && npm run build`). Sicherheits-/Release-Modell identisch; nur Install/Restart-Primitive unterscheiden sich. Referenz-Scaffolding, nicht blind emittiert.

## Dateien dieses Skills

- `scripts/install.sh` — der Generator.
- `assets/python-systemd/` — templatete Quell-Dateien (Platzhalter: `__APP_SLUG__`, `__PKG_NAME__`, `__GITHUB_REPO__`, `__SERVICE_USER__`, `__APP_PORT__`, `__TZ__`).
- `assets/nextjs/` — Node-Variante (Referenz).
- `references/` — Konventionen, Architektur, Next.js-Notizen.
