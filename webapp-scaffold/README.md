# webapp-scaffold — so nutzt ein Agent das (Nutzungs-Doku)

**Zweck:** Ein Agent bekommt per einem Befehl eine konsistente, deploybare Web-App
(Next.js 15 + LXC-Deploy auf Port 80) — optional mit durchsuchbarer Wissensdatenbank.

## TL;DR für den Agenten

```bash
# Mit KB (durchsuchbare Wissensdatenbank):
bash ~/.claude/skills/webapp-scaffold/assets/scaffold.sh \
  --name docs-app --target ~/projects/docs-app \
  --title "Docs Knowledge Base" --desc "Project documentation & search." --with-kb

# Ohne KB (reine Web-App):
bash ~/.claude/skills/webapp-scaffold/assets/scaffold.sh \
  --name foo-master --title "Foo Dashboard"

# Mit GitHub-Modul (offene Issues + PRs des eigenen Repos):
bash ~/.claude/skills/webapp-scaffold/assets/scaffold.sh \
  --name foo-master --title "Foo Dashboard" --with-github --github-repo <your-org>/<your-app>
```

Danach: `cd <target> && pnpm install && pnpm build`. Bei KB: Enums in `scripts/schema.sql`
+ `scripts/ingest.py` für die Domäne füllen, `content/` befüllen, ingesten. Details: `SKILL.md`.

## Was rauskommt

- Next.js-15-App (App Router, schwarzes Minimal-Theme), `output: standalone`.
- `scripts/deploy.sh` (idempotent) + systemd-Unit + nginx-Vhost (Port 80) — LXC-Deploy-Pattern.
- Mit `--with-kb`: pgvector-Hybrid-Suche (RRF), lokaler e5-Embedding-Service, Ingest-Pipeline,
  Such-UI + Artikel-Detailseite. KB-Pipeline kommt aus dem `knowledge-base`-Skill (kein Duplikat).
- Mit `--with-github`: Seite `/github` + `GET /api/github` mit den offenen Issues + PRs des eigenen
  Repos (GitHub-REST, kein `gh`-CLI nötig). Config via `.env.local`: `GITHUB_REPO` + `GITHUB_TOKEN`
  (PAT, Pflicht für private Repos). Single-repo/read-only — keine Multi-Repo-Triage.
- **Einheitliche Versionslogik** (Konvention): `package.json` als kanonische Semver-Quelle (0.1.0),
  `CHANGELOG.md` (Keep-a-Changelog) + `scripts/bump-version.sh` (Conventional-Commits → semver + Git-Tag).
  Release: `bash scripts/bump-version.sh && git push --follow-tags`. Tag = Version → triggert
  `webapp-auto-updater`. Details + Bump-Tabelle: `SKILL.md` (Abschnitt „Versionierung & Release").

## Referenz-Implementierung

A production app was built with exactly this stack and serves as the living reference
(your own `<your-org>/<your-app>`, live on an LXC at port 80). A domain extension of the
schema (validity range `valid_from`/`valid_to` + `legal_basis`) shows how to extend `db.ts`
and the schema for your own fields.

## Contributing

Improvements or generally-useful domain extensions are welcome — open an issue or PR.

## Bekannte Grenzen / TODO (inkrementell)

- Kategorie-Browsing + Stats-Kacheln sind in der Referenz (stromnetz) reicher als im Scaffold;
  bei Bedarf von dort übernehmen.
- Kein eingebautes Auth — bei nicht-öffentlichen Apps `webapp-chat-bridge`/eigenen Schutz davor.
- Cross-Encoder-Reranker (optionaler Qualitätssprung) nicht enthalten — siehe knowledge-base
  `references/rag-architecture.md` als Swap-in-Punkt.
