# webapp-scaffold — Architektur & Erweiterungspunkte

## Data-Flow (mit KB)

```
content/**/*.md (knowledge-base-Artikel-Standard)
   │  scripts/ingest.py  (parse · validate enums · heading-chunk · multi-vector embed)
   ▼
scripts/embedding-service.py  (multilingual-e5-small, 384-dim, query:/passage:, L2-norm)
   ▼
PostgreSQL + pgvector   documents + chunks(embedding + tsvector)
   │  src/lib/db.ts  hybridSearch() — RRF(k=60) · best-per-doc · trust boost
   ▼
Next.js  /  ·  /doc/[slug]  ·  /api/search   (systemd → nginx :80)
```

Ohne KB entfällt alles ab `content/`; es bleibt die Next.js-Shell + Deploy-Schicht.

## Warum dieser Stack

- **Next.js standalone** → ein `node server.js`-Artefakt, sauber per systemd. Kein PM2/Docker nötig.
- **Ein Postgres für alles** (Facetten + Vektoren) → kein separater Vektor-Dienst, transaktional.
- **RRF statt Score-Mix** → robust gegen die unvergleichbaren Skalen von Cosine vs. ts_rank.
- **e5-small lokal** → mehrsprachig (DE/EN), klein genug für 4 GB LXC, keine Cloud/API-Keys.
- **nginx :80 → :PORT** → einheitlich, TLS/!Auth bei Bedarf hier ergänzbar.

## Erweiterungspunkte

- **Domänen-Spalten** (z.B. `valid_from`/`valid_to`/`legal_basis`):
  in `scripts/schema.sql` ergänzen, in `src/lib/db.ts` `DOC_FIELDS` + Mapping erweitern,
  optional in den Trust-Boost-CASE aufnehmen (z.B. „aktuell gültig" ×1.05).
- **Kategorie-Browsing / Stats-Kacheln**: `src/lib/categories.ts` + `/kategorie/[slug]` aus der
  aus deiner Domäne übernehmen.
- **Reranker** (Qualitätssprung): Cross-Encoder zwischen RRF und Ausgabe — siehe knowledge-base
  `references/rag-architecture.md`.
- **Auth / Chat / Auto-Update**: `webapp-chat-bridge` bzw. `webapp-auto-updater` nach dem Scaffold.
- **Embedding-Modell tauschen**: `EMBED_MODEL`-Env + `vector(384)`-Dim in schema.sql anpassen, neu ingesten.

## Deploy-Vertrag (LXC)

`scripts/deploy.sh` ist idempotent und erwartet auf dem Container: Node 22 + pnpm (corepack),
nginx; bei KB zusätzlich PostgreSQL + pgvector/pg_trgm + python3-venv. Es stellt DB/Rolle/Schema
**inkl. GRANTs** sicher (Schema wird als `postgres` geladen, App-Rolle braucht Rechte — sonst
„permission denied" beim Ingest), baut standalone, ingestet, setzt systemd + nginx-Vhost.
