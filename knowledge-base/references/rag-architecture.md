# RAG-Architektur — der Retrieval-Stack und warum jedes Teil existiert

> Status: Referenz. Beschreibt die **bewährte** Architektur, auf der Schema, Ingest und
> Suche aufsetzen. Generalisiere, nicht neu erfinden. Die konkreten Assets liegen in
> `../assets/` (`schema.sql`, `ingest.py`, `embedding-service.py`).

## Überblick — Data-Flow

```
Autor schreibt Markdown (KB-Artikel-Standard, frontmatter + body)
        │
        ▼
ingest.py  ── parse frontmatter (PyYAML) ─ validate closed enums (fail loud)
        │     heading-aware chunking (H1/H2/H3, ~1800-char secondary cap)
        │     deterministic context-prepend (nur fürs Embedding)
        │     multi-vector: body(passage) + summary(passage) + je question(query)
        ▼
embedding-service.py  ── multilingual-e5-small, 384-dim, query:/passage:-Prefix, L2-norm
        │
        ▼
PostgreSQL + pgvector ── documents (frontmatter-as-schema) + chunks (embedding + tsvector)
        │
        ▼
hybridSearch()  ── pgvector(HNSW cosine) ∥ tsvector(FTS)  → RRF(k=60)
        │           → best-per-doc dedupe → trust boost
        ▼
Agent / UI  ── Top-k Hits (snippet + slug + article_type + status)
```

## Die Bausteine (und warum)

### 1. PostgreSQL + pgvector (HNSW cosine)
Ein Store für **beides** — relationale Frontmatter-Facetten (Pre-Filter-Spalten) und
Vektoren. Kein separater Vektor-DB-Dienst nötig. HNSW-Index mit `vector_cosine_ops` für
schnelle ANN-Suche. Facetten (`category`, `article_type`, `scope`, `status`) sind normale
indizierte Spalten → optionale `WHERE`-Pre-Filter schrumpfen das Kandidatenset.

### 2. tsvector-FTS, mit RRF fusioniert
Semantik allein verfehlt **exakte** Strings (Versions-Tokens, Befehle, IDs, Error-Messages).
Der lexikalische Arm (`to_tsvector('simple', …)` + `plainto_tsquery`) fängt sie. Beide
Ranglisten werden mit **Reciprocal Rank Fusion** verschmolzen: `score = Σ 1/(k + rank)`,
**k=60** (Cormack et al.). RRF braucht keine Score-Kalibrierung zwischen den Armen — es
zählt nur der Rang — und ist darum robust gegen die unvergleichbaren Skalen von Cosine vs
ts_rank.

### 3. multilingual-e5-small Embeddings
384-dim, mehrsprachig (DE+EN in einem Raum). **Harte Verträge:**
- **`query:`-/`passage:`-Prefix** — Queries und frageförmige Surfaces als `query:`,
  deklarativer Body als `passage:`. Falscher/fehlender Prefix halbiert die Qualität lautlos.
- **L2-Normalisierung** aller Vektoren (`normalize_embeddings=True`) — sonst ist
  pgvector-Cosine bedeutungslos.
- **512-Token-Ceiling** (`max_seq_length = 512`). Der Body-Cap (~1800 Zeichen) hält
  Context-Prefix + Body darunter; größere Chunks würden am Tail un-embedded.

### 4. Multi-Vector pro Artikel
Statt nur Body-Chunks bekommt jeder Artikel zusätzliche „virtuelle" Rows
(`chunk_kind`):
- **`body`** — heading-split Sektion (`passage:`), `heading_path`='A', content='B'.
- **`summary`** — die ≤50-Wort-Zusammenfassung als eigene `passage:`-Row (answer-first).
- **`question`** — eine Row **je** `questions[]`-Eintrag, als `query:` embedded.
Question-Rows sind das HyPE-lite-Herzstück: eine einkommende Query (`query:`) matcht
question-förmige Surfaces enger als deklarativen Body (Question→Question >
Question→Statement). **Nur Fragen, nie generierte Antworten** speichern → keine
halluzinierten Pfade. Nach RRF wird auf **einen Treffer pro Doc** dedupliziert (best
score über alle Rows), angezeigt aber der beste **body**-Chunk.

### 5. Deterministischer Context-Prepend (Contextual Retrieval)
Vor dem Embedding wird jedem Body-Chunk ein Kontext-Präfix vorangestellt, rein
deterministisch aus Frontmatter + `heading_path` gebaut (kein LLM):
`[<title> · <category> · <device> · <version> · Abschnitt: <heading_path>] <body>`.
**Embedded** wird Präfix+Body, **gespeichert/zurückgegeben** der **rohe** Body. Das gibt
Sekundär-Splits, die ihr Heading verloren haben, ihren Scope zurück (Anthropic Contextual
Retrieval, Zero-LLM-Variante).

### 6. chunk_kind-aware tsvector-Gewichtung
Im Schema generiert (`GENERATED ALWAYS AS … STORED`):
- `summary`/`question`-Rows: gesamter Inhalt Gewicht **'A'** (reine Frage-Surfaces).
- `body`-Rows: `heading_path` Gewicht **'A'**, `content` Gewicht **'B'**.
So ziehen frageförmige Surfaces und Headings im FTS-Arm mehr Gewicht — dieselbe Logik
wie im semantischen Arm, nur lexikalisch.

### 7. Trust Boost (Status + Quelle + Freshness)
Nach RRF wird der Score multiplikativ gebiased (in `hybridSearch`):
- `status = verified` → ×1.15; `unverified-reprint` → ×0.9; `deprecated/archived` → ×0.5.
- offizielle Quelle → ×1.05 (operationalisiert „verifizierte Pfade gewinnen Ties").
- stale `site-state` (`verified_at` älter als ~180 Tage) → ×0.8.
Das ist der operative Hebel hinter der No-Halluzination-Garantie: verifizierte, frische,
offizielle Inhalte ranken vor unsicheren.

## Deployment-Notizen

- **Embedding-Service** = kleiner FastAPI-Wrapper (`embedding-service.py`), lokal auf
  `127.0.0.1:8765`, **nicht** nach außen exponiert. Als **systemd**-Unit (oder launchd
  auf macOS) betreiben; lädt das Modell einmal im Lifespan. Ingest und Such-Layer sprechen
  ihn per HTTP an (`/embed`, `kind: query|passage`).
- **Ingest** = idempotentes, re-runnbares Script (`ingest.py`). Re-Ingest ersetzt alle
  Chunks eines Docs, behält aber die Doc-ID (`ON CONFLICT (slug) DO UPDATE`). `--prune`
  löscht Docs, deren Quelldatei weg ist. Validierung failt laut bei unbekannten Enums.
- **Abhängigkeiten Ingest:** `psycopg`, `httpx`, **PyYAML** (robuste Frontmatter;
  fällt sonst auf einen Mini-Parser zurück), optional `pypdf` für PDF-Quellen.
  Embedding-Service: `fastapi`, `uvicorn`, `sentence-transformers`.
- **DB-Setup:** `schema.sql` einmal einspielen (`CREATE EXTENSION vector, pg_trgm`).
  `DATABASE_URL` und `EMBED_URL` als Env-Vars; **kein Passwort hardcoden**.

## Swap-in-Punkte (was austauschbar ist, ohne den Rest umzubauen)

- **Embedding-Modell:** `EMBED_MODEL`-Env in `embedding-service.py` setzen. Größeres e5
  (`multilingual-e5-base/-large`) oder ein anderes Modell — dann **die Vektordimension in
  `schema.sql` (`vector(384)`) anpassen** und neu ingesten. Prefix-Vertrag ggf. anpassen
  (nicht-e5-Modelle brauchen evtl. keine `query:`/`passage:`-Prefixe).
- **Cross-Encoder-Reranker (optional):** zwischen RRF und finaler Ausgabe einen kleinen
  multilingualen Reranker (z. B. `bge-reranker-v2-m3`) schalten: Stage-1-Fenster ~20
  Kandidaten, Reranker entscheidet die finale Ordnung, Top 3–5 behalten. Größter
  Qualitätssprung nach den Content-Maßnahmen (Anthropic: 49 %→67 % weniger Failures).
- **RRF-Gewichtung/Biasing:** `RRF_K` und die Trust-Boost-Faktoren in `hybridSearch`
  sind frei justierbar; per Query-Intent `article_type` bevorzugen (how-to vs reference).
- **FTS-Sprache:** `'simple'`-Config ist sprach-neutral (gut für DE+EN-Mix). Für eine
  einsprachige Domäne kann ein Stemming-Dictionary (`'german'`/`'english'`) den FTS-Arm
  verbessern.
