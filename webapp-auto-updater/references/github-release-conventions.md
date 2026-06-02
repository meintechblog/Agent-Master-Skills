# GitHub-Release-Konventionen (Pflichtlektüre vor dem ersten Release)

Der Updater ist **release-getrieben**. Wenn diese Konventionen nicht eingehalten werden, sieht
keine Box ein Update — genau die Falle, in die wir selbst getappt sind (kein Release > Code-Stand
geschnitten → Updater meldete „aktuell", obwohl neuer Code da war).

## 1. Echtes Release, kein bloßer Tag
Detection läuft über `GET /repos/<owner>/<repo>/releases/latest`. Ein reiner `git tag` taucht dort
**nicht** auf. Es muss ein **Release** existieren. Prereleases werden gefiltert (der Client überspringt
`prerelease: true`).

## 2. Semver-Tags im Lockstep mit pyproject
- Tag-Format: `vMAJOR.MINOR.PATCH` (führendes `v` ok), z.B. `v8.1.0`. Der Parser (`updater/version.py`,
  `Version.parse`) akzeptiert nur 2- oder 3-Feld-Dotted-Integer. Pre-Release-Suffixe (`-rc1`) → Parse-Fehler.
- `pyproject.toml [project].version` **muss** mit dem Tag übereinstimmen und gleichzeitig gebumpt werden.
  Die laufende Version kommt aus `importlib.metadata.version(<app-slug>)` — also aus pyproject, nicht aus dem Tag.
  Divergieren Tag und pyproject, vergleicht der Updater Äpfel mit Birnen.

## 3. Release am main-HEAD schneiden
```bash
# erst Code auf main:
git push origin main
# dann Release am aktuellen main-HEAD:
gh release create vX.Y.Z --target main --title "vX.Y.Z" --notes "…"
```
Der privilegierte Installer akzeptiert **nur SHAs, die Ancestor von `origin/main` sind**
(`updater_root/git_ops.is_sha_on_main`). Wir schneiden jedes Release am main-HEAD, also IST der
neueste main-Commit der Release-Commit. `resolve_remote_main_sha()` löst `origin/main` HEAD als
Install-Ziel auf. Folge der Reihenfolge: **mergen → pushen → taggen.** Niemals ein Release auf einen
Branch zeigen lassen, der nicht in main gemerged ist — der Installer lehnt den SHA sonst ab.

## 4. Deploy-Modell: git-Clone, nicht rsync
Die Ziel-Box muss ein **git-Clone** sein (blue-green via `install.sh`), weil der Installer per
`git fetch` + `git checkout <sha>` in eine frische Release-Dir auscheckt. Eine rsync-deployte Box hat
kein `.git` → der git-basierte Updater kann nicht auschecken. In der config.yaml:
```yaml
update:
  enabled: true
  github_repo: owner/repo
  check_interval_hours: 24
  auto_install: true      # nächtliches 04:00-Auto-Update (Default an)
```

## 5. Betreiber-Cheatsheet (der ganze Release-Flow)
```
1. Feature-Branch → review → merge in main
2. pyproject.toml [project].version bumpen (semver)
3. git commit + git push origin main
4. gh release create vX.Y.Z --target main --notes "…"
5. fertig — alle Instanzen ziehen beim nächsten Check oder um 04:00 (TZ) automatisch nach.
   Erst-Bootstrap / Hosts die noch alten Updater-Code fahren: einmalig install.sh re-run.
```

## 6. Typische Fehler (und Symptom)
| Fehler | Symptom |
|---|---|
| Nur getaggt, kein Release | Updater meldet dauerhaft „aktuell" |
| Tag ≠ pyproject-Version | falscher Versionsvergleich, Update wird nicht/zu oft angeboten |
| Release zeigt auf nicht-gemergten Branch | Installer lehnt SHA ab (`not an ancestor of origin/main`) |
| Prerelease angelegt | wird gefiltert, kein Update |
| Box per rsync deployed | Installer findet kein `.git`, Checkout schlägt fehl |
| `update.enabled=false` | Scheduler startet nie, Buttons verweigern |
