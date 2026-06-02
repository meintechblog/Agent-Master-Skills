# Architektur des Updaters

## Zwei-Prozess-Trust-Boundary
Die Web-App läuft als unprivilegierter User (`User=<service-user>`) und darf weder nach `/opt`
schreiben noch Dienste neu starten. Update-Ablauf:

1. **Web-App (unprivilegiert):** User klickt „Installieren" → `routes_updater.py` ruft
   `updater/trigger.py:write_trigger()` → schreibt validierten JSON-Trigger nach
   `/etc/<slug>/update-trigger.json` (Ziel-SHA, Nonce, Schema-Version).
2. **systemd `.path`-Unit** (`<slug>-updater.path`) watcht diese Datei (`PathModified=`) und aktiviert
   bei Änderung die **root**-`oneshot`-Unit `<slug>-updater.service`.
3. **Root-Installer** (`updater_root/__main__.py` → `runner.py`):
   - Trigger lesen + validieren (`updater_root/trigger_reader.py`, Nonce-Dedup gegen Replay).
   - `git fetch`; verifizieren dass Ziel-SHA **Ancestor von origin/main** (`git_ops.is_sha_on_main`).
   - Disk-Space-Check; Backup/Retention der alten Releases.
   - **blue-green:** frischen Checkout in neue Release-Dir (`/opt/<slug>-releases/<sha>`),
     venv + `pip install`, `compileall` + `smoke_import` + `config_dryrun` (Pre-Flight, KEIN Flip bei Fehler).
   - **atomarer Symlink-Flip** `/opt/<slug>` → neue Release-Dir; `PENDING`-Marker schreiben.
   - `systemctl restart <slug>.service`; **Healthcheck** (60s, GET `http://127.0.0.1:<port>/api/health`,
     prüft erwartete Version/Commit).
   - Healthcheck grün → `PENDING`-Marker clearen, Exit 0. Rot → Symlink zurückflippen + Restart (Rollback),
     Exit 2. Rollback selbst gescheitert → Exit 3 (CRITICAL, manueller SSH).
4. **Recovery-Unit** (`<slug>-recovery.service`, beim Boot): findet ein liegengebliebener `PENDING`-Marker
   (Box mitten im Update gecrasht) → flippt Symlink auf den letzten guten Release zurück.

## KRITISCH: kein cgroup-Coupling
Die Units `<slug>-updater.{service,path}` dürfen **kein** `Requires=`/`BindsTo=`/`PartOf=`
`<slug>.service` haben. Sonst tötet systemd den Updater mitten im `systemctl restart` (er liefe in der
cgroup des sterbenden Hauptdienstes). Die `.path`-Unit muss über Hauptdienst-Restarts hinweg aktiv
bleiben. Das ist in den Template-Units bereits so kommentiert — beim Anpassen nicht entfernen.

## Versions-Quelle
- Laufende Version: `importlib.metadata.version(<app-slug>)` (aus pyproject, KEIN Modul-Import).
- Production-Commit-Fallback: `deploy.sh` schreibt die Short-SHA in `src/<pkg>/COMMIT` (für rsync-Hosts
  ohne `.git`); `version.py:get_commit_hash()` liest sie, wenn `git rev-parse` scheitert.
- Install-Ziel: `resolve_remote_main_sha()` → voller 40-Zeichen `origin/main` HEAD.

## App-Wiring (manuell, app-spezifisch)
- `webapp/app.py`: `register_update_routes(app, config_path=…)`.
- `__main__.py`: beim Boot Scheduler-Task (`updater/scheduler.py`) + Nightly-Task
  (`updater/auto_update.py`) starten, beim Shutdown canceln; `_on_update_available`-Callback durchreichen,
  der den verfügbaren SHA der UI sichtbar macht.
- pyproject: `[project].name == <app-slug>`, `[project].version` semver.

## Sub-Module-Übersicht
`updater/`: version, config, github_client, scheduler, auto_update, trigger, security, status,
progress, maintenance. `updater_root/`: __main__, runner, git_ops, pip_ops, healthcheck, backup,
trigger_reader, status_writer, gpg_verify (optional Signatur-Check), __init__ (Trust-Boundary-Doku —
dieses Paket darf NUR releases/recovery/state_file aus dem App-Paket importieren, nie webapp/__main__).
