---
name: webapp-scaffold
description: "Scaffolde eine konsistente, deploybare Web-App für dein Projekt-Repo — Next.js 15 (App Router, schwarzes Minimal-Theme) auf dem LXC-Standard-Stack (native Node + systemd + nginx, Port 80), mit OPTIONALEM Wissensdatenbank-Modul (PostgreSQL+pgvector Hybrid-Suche, RRF, multilingual-e5). Generisch parametrisiert (Projektname/Port/DB/KB an-aus). Nutze diesen Skill, wenn ein Agent per einfachem Auftrag 'erstell eine Web-App für unser Projekt' eine lauffähige, konsistente App inkl. 1-Befehl-Deploy bekommen will. Das KB-Modul referenziert den knowledge-base-Skill (Schema/Ingest/Embedding) statt ihn zu duplizieren."
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Grep
  - Glob
---

<objective>
Aus einem kurzen Auftrag eine **konsistente, deploybare Web-App** erzeugen: Next.js 15
(App Router, schwarzes Minimal-Theme), die nach dem LXC-Deploy-Pattern läuft (native Node +
systemd + nginx, Port 80) — **optional** mit einem Wissensdatenbank-Modul (Hybrid-Suche
pgvector+tsvector, RRF, multilingual-e5-small), das auf den `knowledge-base`-Skill aufsetzt.

Destilliert aus einer real gebauten Produktions-App mit diesem Stack.
Ein `scaffold.sh` generiert das Repo generisch (Parameter: Name, Port, DB, KB an/aus).
</objective>

## Wann diesen Skill nutzen

- Ein Agent soll für sein Repo eine **Web-App** bauen und will den Standard-Stack
  (Next.js + LXC-Deploy auf Port 80) statt selbst zu erfinden.
- Optional mit **durchsuchbarer Wissensdatenbank** („auf natürlichsprachige Fragen die
  relevanten Inhalte zurück"). Dann `--with-kb`.
- Nicht dafür: ein reiner Daemon/CLI ohne UI, oder eine bestehende App umbauen (dann gezielt
  einzelne Assets übernehmen, nicht neu scaffolden).

## Verhältnis zu anderen Skills (NICHT duplizieren)

- **`knowledge-base`** — liefert das KB-Artikel-Schema + die Python-Pipeline
  (`schema.sql`, `ingest.py`, `embedding-service.py`). `--with-kb` **kopiert diese von dort**
  und ergänzt nur die fehlende **Web-Schicht** (TS-Hybrid-Suche `db.ts`, `/api/search`, UI).
- **`webapp-chat-bridge`** — optionale Browser-Chat-UI zur laufenden Claude-Session. Nach dem
  Scaffold separat einbauen, wenn gewünscht.
- **`webapp-auto-updater`** — optionaler One-Click/Nightly-Update-Mechanismus. Nach dem
  Scaffold separat einbauen, wenn gewünscht.

## Stack (warum)

- **Next.js 15 standalone** — ein `node server.js`-Artefakt, sauber per systemd betreibbar.
- **PostgreSQL + pgvector + pg_trgm** (nur KB) — ein Store für relationale Facetten **und**
  Vektoren; kein separater Vektor-Dienst.
- **Hybrid-Suche, RRF (k=60)** — pgvector(HNSW cosine) ∥ tsvector(FTS), rang-fusioniert; robust
  ohne Score-Kalibrierung. Multi-Vector-Chunks (body/summary/question), Trust-Boost.
- **multilingual-e5-small (384-dim)** lokal (FastAPI), `query:`/`passage:`-Prefixe, L2-norm.
- **nginx** Port 80 → `127.0.0.1:<port>`; **systemd** für App (+ Embedding-Service bei KB).

## Vorgehen

### 1. Scaffold generieren
```bash
bash ~/.claude/skills/webapp-scaffold/assets/scaffold.sh \
  --name <repo>-master --target ~/codex/<repo>-master \
  --title "<Titel>" --desc "<ein Satz>" [--port 3000] [--with-kb] [--with-github [--github-repo owner/name]]
```
Erzeugt: `package.json`, `tsconfig.json`, `next.config.ts`, `.gitignore`, `src/app/{layout,page,globals.css}`,
und die Deploy-Schicht (`scripts/deploy.sh`, `scripts/<name>.service`, `scripts/nginx-<name>.conf`).
Bei `--with-kb` zusätzlich: `src/lib/{db,embed}.ts`, `src/components/SearchBox.tsx`,
`src/app/api/search/route.ts`, `src/app/doc/[slug]/page.tsx`, `scripts/{schema.sql,ingest.py,embedding-service.py,requirements.txt,<name>-embedding.service}`, `content/`.
Bei `--with-github` zusätzlich: `src/lib/github.ts`, `src/app/api/github/route.ts`, `src/app/github/page.tsx`, `.env.example` + Header-Nav-Link.

#### GitHub-Modul (`--with-github`)
Repo-parametrisierte Übersicht der **offenen Issues + PRs des EIGENEN Repos** (Seite `/github` + `GET /api/github`).
Bewusst **minimal & single-repo, read-only** über die GitHub-REST-API (kein `gh`-CLI nötig → läuft auf einem nackten LXC).
Das ist bewusst ein einfaches, repo-lokales Read-only-GitHub-Modul — keine Multi-Repo-Triage (Whitelist/Delivery/Multi-Repo-Scan).
Config via ENV in `.env.local` (aus `.env.example`): `GITHUB_REPO=owner/name` + `GITHUB_TOKEN` (PAT mit repo-Scope, **Pflicht für private Repos**; public geht ohne, 60 req/h). Fehlt der Zugriff → die Seite zeigt einen ruhigen Hinweis statt zu crashen.

### 2. (Nur KB) Domänen-Taxonomie füllen
In `scripts/schema.sql` UND `scripts/ingest.py` die `source_type`/`category`-Enums für deine
Domäne setzen (siehe `knowledge-base`-Skill §1). Sie werden beim Ingest hart validiert.
Optional Domänen-Spalten ergänzen (z.B. `valid_from`/`legal_basis`) —
dann auch in `src/lib/db.ts` `DOC_FIELDS` erweitern.

### 3. Lokal hochziehen
```bash
cd ~/codex/<repo>-master && pnpm install
# nur KB:
createdb <db> && psql -d <db> -c 'CREATE EXTENSION vector; CREATE EXTENSION pg_trgm;'
psql -d <db> -f scripts/schema.sql
python3 -m venv .venv-embed && .venv-embed/bin/pip install -r scripts/requirements.txt
.venv-embed/bin/python -m uvicorn --app-dir scripts embedding-service:app --port 8765 &
# content/ befüllen (knowledge-base-Artikel-Standard), dann:
DATABASE_URL=postgres://localhost:5432/<db> EMBED_URL=http://127.0.0.1:8765 \
  .venv-embed/bin/python scripts/ingest.py --content ./content --prune
pnpm dev   # http://localhost:3000
```

### 4. Deploy auf den LXC (Port 80)
Einen LXC bereitstellen (Node 22 + pnpm, nginx; bei KB: PostgreSQL+pgvector + python3-venv).
GitHub-Repo + Deploy-Key, dann:
```bash
ssh root@<lxc-ip>
git clone <repo-ssh-url> /opt/<repo>-master
bash /opt/<repo>-master/scripts/deploy.sh   # idempotent: DB/Schema/Rechte, Embedding, build, ingest, nginx
```

### 5. Eval (nur KB)
Ein paar echte Fragen gegen `/api/search` testen (Top-Treffer = erwarteter Artikel?). Schwache
Queries → `questions`/`aliases` im Artikel-Frontmatter nachziehen oder Gap-Artikel ergänzen.

### 6. Versionierung & Release (Konvention)
Jede generierte App bekommt die einheitliche Semver-Logik ab Start (Policy
der Semver-Konvention). **`package.json` `"version"` ist die EINE kanonische Quelle**
(startet `0.1.0`). Pro abgeschlossener Einheit: Version bumpen + `CHANGELOG.md`-Eintrag
(Keep-a-Changelog, datiert) + Git-Tag `vX.Y.Z`.

Bump-Regel (Conventional Commits, höchster gewinnt):

| Commit-Prefix | Bump |
|---|---|
| `feat!:` / Body „BREAKING CHANGE:" | MAJOR (Pre-1.0 → MINOR; `1.0.0` nur bewusst) |
| `feat:` | MINOR |
| `fix:` / `perf:` | PATCH |
| `docs`/`chore`/`refactor`/`test`/`style`/`ci`/`build` | kein Bump |

Tooling-frei — der mitgenerierte Helper macht alles aus den Commits seit dem letzten Tag:
```bash
bash scripts/bump-version.sh        # auto (oder --bump minor / --dry-run / --no-tag)
git push --follow-tags
```
**Verzahnung mit `webapp-auto-updater`:** der Tag IST die Version. Ein GitHub-Release am main-HEAD
(`gh release create vX.Y.Z --target main`) ist das Signal, das alle Instanzen ziehen — der Updater
erkennt genau diese Releases. Reihenfolge immer: **mergen → bumpen → pushen → Release am main-HEAD.**

## Parameter (für den Rollout)

| Flag | Default | Zweck |
|---|---|---|
| `--name` | (Pflicht) | Repo-/App-/systemd-/nginx-/DB-Name-Basis |
| `--target` | `~/codex/<name>` | Zielverzeichnis |
| `--title` / `--desc` | `<name>` | UI-Titel + Beschreibung |
| `--port` | `3000` | interner App-Port (nginx mappt 80 → Port) |
| `--embed-port` | `8770` | Port des Embedding-Service. **NIE 8765** (häufig vom Speech-Service belegt, s.u.) |
| `--db-name` / `--db-user` | aus `--name` | Postgres-DB + Rolle (nur KB) |
| `--with-kb` | aus | KB-Modul (pgvector Hybrid-Suche) mitscaffolden |
| `--with-github` | aus | GitHub-Modul (offene Issues + PRs des eigenen Repos, REST, `/github` + `/api/github`) |
| `--github-repo` | `<your-org>/<name>` | Repo `owner/name` fürs GitHub-Modul (überschreibt Default) |

> ⚠ **Port 8765 wird häufig vom lokalen Speech/TTS-Service (Piper/whisper) belegt.** Der
> Embedding-Service darf NIE auf 8765 laufen — sonst gewinnt der lokale 127.0.0.1-Bind und
> killt systemweit die lokale Sprachausgabe (der Voice-Dienst fällt still auf Text). Default ist 8770;
> `scaffold.sh` lehnt `--embed-port 8765` ab. (Gelernt 2026-05-31, realer Vorfall.)

## Assets

- `assets/scaffold.sh` — der Generator (parametrisiert, sed-Substitution).
- `assets/app/*` — Next.js-Shell-Templates (layout, globals.css, page-kb/page-plain, doc-page, SearchBox, api-search-route, package.json, tsconfig, next.config) + `CHANGELOG.md.tmpl` + `bump-version.sh` (Semver-Helper).
- `assets/db/{db.ts,embed.ts}` — generische TS-Hybrid-Suche (RRF) gegen das knowledge-base-Schema.
- `assets/deploy/*` — `deploy.sh`, systemd-Units (app + embedding), nginx-Vhost (alle parametrisiert).
- `references/architecture.md` — Stack-Begründung + Erweiterungspunkte.

## Anti-Patterns

- KB-Pipeline (schema/ingest/embedding) duplizieren statt aus `knowledge-base` zu kopieren.
- e5-`query:`/`passage:`-Prefixe vergessen oder Vektoren nicht normalisieren → halbe Suchqualität.
- `output: "standalone"` vergessen → systemd-Unit findet kein `server.js`.
- Schema als `postgres` laden, App als andere Rolle laufen lassen, GRANTs vergessen → „permission denied" beim Ingest (deploy.sh setzt die Rechte; bei manuellem Setup dran denken).
