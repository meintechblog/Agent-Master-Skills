---
name: knowledge-base
description: "Baue eine professionelle, RAG-optimierte Wissensdatenbank zu einem beliebigen Themengebiet — und überführe bestehende Inhalte (Doku, Reprints, Findings, Logs, PDFs) in saubere, einheitliche, frageorientierte Wissensartikel. Liefert: ein kanonisches Artikel-Schema (Diátaxis-Typen + Frontmatter-as-Schema), die Hybrid-Retrieval-Architektur (PostgreSQL+pgvector, multilingual-e5, RRF, Multi-Vektor, Context-Prepend), und ein Multi-Agent-Playbook (Audit → Restructure-Plan → Transform → Verify → Ingest). Nutze diesen Skill, wenn ein Agent eine eigene Themen-KB aufbauen oder vorhandene Inhalte in eine einsetzbare, durchsuchbare Wissensbasis bringen will, in der man auf natürlichsprachige Fragen die relevanten Inhalte zurückbekommt."
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Grep
  - Glob
  - WebSearch
  - WebFetch
  - Agent
  - Workflow
---

<objective>
Aus vorhandenen Inhalten zu einem Themengebiet eine **professionelle Wissensdatenbank**
machen: einheitlich strukturierte, atomare, frageorientierte Artikel in einer
PostgreSQL+pgvector-Hybrid-Suche, sodass eine natürlichsprachige Frage („wie mache ich X?",
„wo stelle ich Y ein?") zuverlässig die richtigen Artikel zurückliefert — für Menschen UND
für Agenten, ohne halluzinierte Fakten.

Dieser Skill ist die destillierte Methode aus einem real gebauten KB-Projekt. Er gibt dir
drei Dinge: ein **Artikel-Schema** (`references/article-standard.md`), die **Methodik/Prinzipien**
(`references/methodology.md`) und die **Retrieval-Architektur + lauffähige Asset-Templates**
(`references/rag-architecture.md` + `assets/`).
</objective>

## Wann diesen Skill nutzen

- Ein Agent will eine **Themen-KB** aufbauen (Hardware, API, Domäne, Produkt, Runbooks …).
- Vorhandene Inhalte (Doku-Reprints, eigene Findings, PDFs, Logs, Live-Configs) sollen in
  eine **einsetzbare, durchsuchbare** Form gebracht werden, statt als Doc-Halde zu liegen.
- Ziel ist **Frage→Antwort-Retrieval**: „bei Fragestellungen die relevanten Inhalte zurück".

Nicht dafür: ein einzelnes README pflegen, eine reine Datei-Suche, oder wenn keine ≥~20 Doks
zusammenkommen (dann lohnt der Vektor-Stack nicht — eine gut strukturierte Markdown-Sammlung
nach `references/article-standard.md` reicht).

## Die 6 goldenen Regeln (verinnerlichen, sie treiben jede Entscheidung)

1. **Keine erfundenen Fakten.** Jeder Artikel kommt aus verifizierten Quellen; was nicht
   belegt ist, wird als `status: unverified-reprint` markiert, nicht geraten.
2. **Ein Artikel = ein `article_type` = eine Frage/Aufgabe** (Diátaxis). Theorie, Prozedur
   und Instanz-Zustand NIE im selben Artikel — gemischte Embeddings ranken überall mittelmäßig.
3. **Single-Sourcing (DRY).** Jeder Fakt lebt an genau einer kanonischen Stelle; andere
   Artikel verlinken ihn (`related`/`canonical_ref`) statt ihn zu kopieren.
4. **Answer-First.** Erste Zeile jedes Artikels und jeder H2 = direkte Antwort (`Kurzantwort:`).
   Der retrievte Chunk muss allein stehen können.
5. **Frageorientiert für Retrieval.** Jeder Artikel trägt `questions:` (3–8 natürlichsprachige
   Fragen, mehrsprachig) + frageförmige Headings — der höchste ROI-Hebel, weil User Fragen
   stellen und Content Aussagen ist.
6. **Trenne wiederverwendbares Wissen von Instanz-Zustand.** Generische how-tos/references
   (evergreen) vs. datierte `site-state`/Inventory (was ist HIER aktuell so) mit `verified_at`.

## Playbook: KB aus bestehendem Content (End-to-End)

### Schritt 0 — Lies die Referenzen
Bevor du irgendwas baust: `references/methodology.md` (das „warum") und
`references/article-standard.md` (das mechanisch anwendbare „wie"). Bei Stack-Fragen
`references/rag-architecture.md`.

### Schritt 1 — Domänen-Taxonomie festlegen
Definiere DEIN controlled vocabulary (das ist domänenspezifisch, der Rest ist generisch):
- **`category`** (8–14 Werte): die eine Topic-Facette, genau eine pro Artikel. Muss für
  Browse UND Suche taugen.
- **`source_type`** (woher stammt der Inhalt): z.B. `official-docs`, `community`, `own-findings`,
  `pdf-manual`, `blog`, `memory`, …
- **`article_type`**: nimm das Default-Set (how-to · reference · concept · troubleshooting ·
  faq · site-state), nur ändern wenn die Domäne es klar verlangt.
Trag diese Enums **identisch** in `assets/schema.sql` UND `assets/ingest.py` ein (sie werden
beim Ingest hart validiert — unbekannter Wert failt laut).

### Schritt 2 — Retrieval-Stack hochziehen
Siehe `references/rag-architecture.md`. Kurz: PostgreSQL + pgvector + `pg_trgm`,
`assets/schema.sql` laden, `assets/embedding-service.py` als lokalen Dienst starten
(multilingual-e5-small, braucht `query:`/`passage:`-Prefixe — schon eingebaut), `assets/ingest.py`
als idempotente Pipeline. PyYAML im Service-venv installieren. Eine bestehende KB-Deployment-
Vorlage wiederverwenden, wenn du eine hast, statt neu zu bauen.

### Schritt 3 — Content-Audit + Restructure-Plan (NICHT direkt drauflos schreiben)
Methodik §8. Erst **Inventory** (ein Script-Pass: pro Doc slug/title/category/vermuteter
article_type/scope/length/origin), dann pro Doc eine **Disposition**: `keep | normalize | split
| merge | retire`. Daraus ein **RESTRUCTURE-PLAN.md**: eine Zeile pro ZIEL-Artikel mit
target_slug, article_type, category, scope, source_docs, canonical_ref. Jedes Quell-Doc muss
verbucht sein (nichts still fallen lassen). Plus eine **Gap-Liste** (welche Artikel eine
professionelle KB hier zusätzlich braucht — nur aus belegbaren Quellen).
> Bei vielen Docs: diesen Plan von EINEM starken Agenten erstellen lassen (Cross-Doc-Dedupe-
> Logik), dann selbst reviewen, BEVOR transformiert wird.

### Schritt 4 — Transformieren (skaliert per Multi-Agent-Workflow)
Jeden Ziel-Artikel nach `references/article-standard.md` schreiben: volles Frontmatter
(inkl. `summary` + `questions` + `applies_to` + `status`), Answer-First-Body, frageförmige
bilinguale Headings, mangled Markdown reparieren, `split`/`merge` umsetzen, site-state nur
Delta + `canonical_ref`. Bei vielen Artikeln das **Workflow-Pattern** nutzen:
`parse Plan → author (1 Agent/Artikel, je nur seine Quellen lesen) → verify (gegen den
Standard)`. Harte Constraints im Author-Prompt: **nur Fakten aus den Quellen, keine erfundenen
Pfade/Werte**; unbelegt → `status: unverified-reprint`. Günstige Modelle (sonnet/haiku) für die
Transformation, dein Session-Modell für Plan-Review.

### Schritt 5 — Ingesten + Retrieval evaluieren
`assets/ingest.py --prune` laufen lassen (ersetzt Chunks idempotent, prunt entfernte Docs).
Dann ein **Eval-Set** echter Fragen (DE+EN, Novice+Expert, Symptom+Lösung) mit erwartetem
Zielartikel gegen die Such-API messen (Recall@k / MRR). Schwache Queries → fehlende
`questions`/`aliases` nachziehen oder Gap-Artikel ergänzen. Vorher/nachher messen, nicht annehmen.

### Schritt 6 — Pflegen
`status`/`verified_at` ehrlich führen; volatile (UI-/pfad-abhängige) Artikel bei jedem
Major-Update re-verifizieren; neue Findings als atomare Artikel anhängen + re-ingesten.

## Assets (in diesem Skill gebündelt)

- `references/methodology.md` — Prinzipien (Diátaxis, KCS, EPPO, Information Mapping,
  Contextual Retrieval, HyPE, faceted taxonomy) + Pitfalls + Audit-Vorgehen.
- `references/article-standard.md` — kanonisches Frontmatter-Schema, Body-Template je
  article_type, worked Example, Transformations-Checkliste, Chunking-Guidance.
- `references/rag-architecture.md` — der Stack + warum jede Komponente da ist + Deploy + Swap-Punkte.
- `assets/schema.sql` · `assets/ingest.py` · `assets/embedding-service.py` — lauffähige,
  generalisierte Pipeline (Taxonomie-Enums als TODO markiert).
- `assets/templates/*.md` — leere, voll strukturierte Skelette je article_type.

## Anti-Patterns

- Inhalte 1:1 reinkippen ohne Audit/Restructure → Doppelungen, gemischte Typen, mieses Ranking.
- `questions`/`summary` als totes YAML lassen → der Ingest faltet sie in Embedding + FTS; ohne
  sie verschenkst du den größten Retrieval-Hebel.
- e5-Prefixe vergessen oder Vektoren nicht normalisieren → lautlos halbierte Qualität.
- Über-Filtern auf kleinem Korpus (category AND type AND status …) → null Treffer; Facetten weich halten.
- Generierte ANTWORTEN statt nur Fragen embedden → so kommen halluzinierte Fakten rein.
