# BACKLOG: `mac-launchd-node`-Modus + compare-API-Detection

> ✅ IMPLEMENTIERT 2026-05-30 als Skill-Modus `mac-launchd-node` (assets/mac-launchd-node/ +
> install.sh `--stack mac-launchd-node --launchd-label …`). Aus einer Produktions-Node-Webapp templatet.
> ⚠ Rollback-Leg weiterhin UNTESTED — Fail-Inject-Test ausstehend. Dieses Dokument bleibt als
> Design-/Mechanik-Referenz.


Quelle: dieser Updater wurde eigenständig für Mac/launchd/Node adaptiert und
geshippt (Release v0.2.0). Diese Innovationen sollen als dritter Skill-Modus + optionale
Detection-Variante in `webapp-auto-updater` zurückfließen. Noch NICHT gebaut — das ist die
Bauanleitung.

## Referenz-Implementierung (public Repo, direkt reinschauen)
Repo: a production Node/launchd webapp
- `scripts/update-apply.mjs` — detached/unref'd Worker, der ganze Apply-Flow (launchd-Adaption)
- `lib/updater.mjs` — Detection + Trigger; speziell `checkForUpdate()` (compare-API "ahead"),
  `startApply()` spawnt den Worker detached
- `server.mjs` — Server-Wiring (Suche "/api/update/" → 3 Routen + `self` in /api/health)
- `public/index.html` — UI-Badge/Poll (Suche "checkHubUpdate")

## Drei Kernpunkte des Modus

### 1) compare-API-Detection (robuster als /releases/latest, killt verwaiste Tags)
- `GET /repos/{repo}/git/ref/tags/{tag}` → `object.sha` (bei annotated tag → deref via
  `GET /repos/{repo}/git/tags/{sha}`)
- `GET /repos/{repo}/compare/{running_sha}...{tag_sha}` → `status === "ahead"` = echtes
  Vorwärts-Update.
- KONSERVATIV: alles ≠ "ahead" (null/behind/identical/diverged) → KEIN Update.
- Vorteil: übersteht verwaiste Tags (z.B. nach PII-History-Rewrite) + no-cadence-Repos, wo
  `/releases/latest` versagt. Als optionale Detection-Strategie neben dem Release-Pfad anbieten.

### 2) Worker-Survival über den Restart hinweg
- Apply-Worker per `spawn(detached:true, stdio:"ignore").unref()` losgelöst → überlebt
  `launchctl kickstart -k gui/<uid>/<label>` (der den Hauptprozess neu startet).
- WICHTIG: der Worker muss SELF-CONTAINED sein (keine Imports aus dem eigenen Repo), weil die
  lib-Files während des Checkouts UNTER ihm getauscht werden. (Analog zum systemd-root-oneshot,
  der außerhalb der cgroup des Hauptdienstes läuft — gleiche Grundidee, andere Mechanik.)

### 3) In-place-Checkout statt /opt-Blue-Green
- `git checkout -B main <sha>` (landet immer auf main@target, egal ob vorher detached).
- Rollback = dasselbe mit der gemerkten Vorgänger-SHA.
- Health-Match über `/api/health.self.commit_full` (Full-SHA-Vergleich, KEIN Versions-String).
- Kein root nötig (user-launchd), kein separates Release-Verzeichnis.

## Unterschiede zum systemd-Pfad (für die Modus-Matrix)
| Aspekt | python-systemd | mac-launchd-node |
|---|---|---|
| Restart | `systemctl restart` (root oneshot via .path) | `launchctl kickstart -k gui/<uid>/<label>` (detached worker) |
| Privileg | root-Trennung (.service/.path) | kein root (user-launchd) |
| Deploy | blue-green /opt + Symlink-Flip | in-place `git checkout -B main` |
| Build | venv + pip | npm ci |
| Restart-Survival | außerhalb cgroup | detached+unref'd, self-contained worker |
| Health | /api/health + version | /api/health.self.commit_full |

## Offener Caveat (noch nicht real getestet)
Rollback-on-failure-Leg (bräuchte ein bewusst kaputtes Release). Mechanik steht: Health-Poll-Timeout
→ checkout zurück (Vorgänger-SHA) → kickstart. Dieselben Primitive rückwärts. Beim Bau des Modus
einen echten Fail-Inject-Test ergänzen.
