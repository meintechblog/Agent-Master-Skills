# KB-Artikel-Standard (kanonisch, kopierbar)

> Status: **maßgeblich**. Jeder Artikel in `content/**/*.md` MUSS diesem Standard folgen.
> Präzise genug, dass ein Agent ihn **mechanisch** anwenden kann.
> Prinzipien/Begründung: `methodology.md`. Retrieval-Stack: `rag-architecture.md`.
>
> **Adapt to your domain:** Zwei Enums musst du selbst füllen — `category` und
> `source_type` (s. Markierungen „**define your own controlled vocabulary**"). Alle
> anderen Enums (`article_type`, `status`, `scope`-Schema, `info_type`) sind der
> empfohlene Default und sollten unverändert übernommen werden.

---

## A. Frontmatter-Schema (YAML)

Jeder Artikel beginnt mit **genau diesem** Frontmatter-Block. Enums werden beim Ingest
validiert — **unbekannter Wert ⇒ laut failen**. Felder ohne Wert weglassen, NICHT leer/null
setzen (außer wo „optional" explizit erlaubt).

```yaml
---
# --- Identität ---
slug:            string        # REQUIRED. stabil, kebab-case. Cross-link-Ziel + URL.
title:           string        # REQUIRED. Frage/Aufgabe in Nutzer-Worten, DE+EN-Keywords in ersten 60 Zeichen.

# --- Klassifikation (geschlossene Enums) ---
article_type:    enum          # REQUIRED. how-to | reference | concept | troubleshooting | faq | site-state
source_type:     enum          # REQUIRED. *** define your own controlled vocabulary for your domain ***
                               #   z.B. official-docs | community-forum | github | manual-pdf | own-findings | blog | memory
                               #   MUSS identisch in schema.sql (CHECK) und ingest.py (ALLOWED_SOURCE_TYPES) stehen.
category:        enum          # REQUIRED. genau EINE. *** define your own controlled vocabulary for your domain ***
                               #   z.B. installation | configuration | api | cli | auth | integrations | troubleshooting | concepts
                               #   MUSS identisch in schema.sql (CHECK, falls aktiviert) und ingest.py (ALLOWED_CATEGORIES) stehen.
scope:           enum          # REQUIRED. general | site:<deine-instanz>   (z.B. site:prod, site:eu, site:kunde-a)
info_type:       enum          # OPTIONAL (Information Mapping): procedure | process | structure | concept | principle | fact

# --- Retrieval-Surfaces (werden in Embedding + tsvector gefaltet!) ---
summary:         string        # REQUIRED. ≤50 Wörter, keyword-dicht, beantwortet den Titel direkt. Eigene 'passage:'-Row.
questions:       string[]      # REQUIRED. 3–8 natürlichsprachige Fragen, DE UND EN gemischt, Novice+Expert. Je eigene 'query:'-Row + tsvector 'A'.
aliases:         string[]      # OPTIONAL. verbatim lexikalische Anker + DE↔EN-Synonyme + exakte Strings. tsvector 'B'.

# --- Geltungsbereich / Verifikation ---
applies_to:                    # REQUIRED-Block
  device:        string        # die "Wahrheits-Einheit" deiner Domäne, z.B. "Tool vX" | "Library 2.4" | "all"
  version:       string        # Version, gegen die Pfade/Befehle verifiziert sind, z.B. "3.12" | "all"
  site:          string        # OPTIONAL, nur bei article_type=site-state: deine Instanz/Umgebung
status:          enum          # REQUIRED. draft | verified | unverified-reprint | deprecated | archived
verified_at:     date          # REQUIRED wenn status=verified|site-state. ISO (YYYY-MM-DD), letzte Pfad-Bestätigung.
owner:           string        # OPTIONAL. benannte Person, kein Team.
review_cadence:  enum          # OPTIONAL. on-major-update | 6-month | annual

# --- Graph / Provenienz ---
related:         string[]      # OPTIONAL. slugs verwandter Artikel (EPPO rich links). Bei Ingest auf dangling geprüft.
canonical_ref:   string        # OPTIONAL, PFLICHT für site-state: slug des generischen how-to, das es referenziert.
source_url:      string        # OPTIONAL. Herkunft für Re-Verifikation.
language:        enum          # REQUIRED. de | en | mixed
last_updated:    date          # REQUIRED. ISO.
ui_paths:        string[]      # OPTIONAL. verifizierte Klick-/Befehlspfade als strukturierte Strings (Exact-Match-Boost + No-Hallu-Audit).
---
```

**Pflicht-Minimum, das jeder transformierte Artikel tragen muss:** `slug`, `title`,
`article_type`, `source_type`, `category`, `scope`, `summary`, `questions`,
`applies_to{device,version}`, `status`, `language`, `last_updated`.

---

## B. Body-Templates pro `article_type`

Universelle Regeln (alle Typen):
- **Erste Zeile nach H1 = `Kurzantwort:`** (Answer-First). Ein Satz, beantwortet den Titel.
- H2/H3 sind **frageförmig + bilingual**, keine bare Nouns. Kein „Schritt 2" → „Schritt 2:
  Wert X und Bereich Y festlegen (set X & Y)".
- **Jede H2-Sektion** beginnt mit `Kurzantwort:` — der retrievte Chunk ist self-sufficient.
- **Keine** relationalen Verweise („wie oben", „continue from previous"). Jeder Pfad
  absolut ab bekanntem Anker: „Settings → Section → Create New".
- Abschluss immer: `## Verwandt / Related` (Liste der `related`-slugs).

> Die sechs Templates unten sind der empfohlene Default. Sie sind anpassbar — aber halte
> die Answer-First- und „ein Typ = ein Level"-Disziplin bei.

### B1. how-to (DITA task-Skelett)
```markdown
# <Aufgabe als Frage/Imperativ> (<EN-Begriff>)
Kurzantwort: <ein Satz, der verifizierte Kernpfad>.

## Beantwortet / Questions this answers
- <Frage DE>
- <question EN>

## Voraussetzungen / Prerequisites
Kurzantwort: <Version/Status/Vorbedingungen>.

## Kontext / When to use
Kurzantwort: <1–2 Sätze wann/warum>.

## Schritte / Steps
Kurzantwort: <Kern-Pfad in einem Satz>.
1. <imperative Aktion, exakter Pfad ab Anker, exakte Labels in Anführungszeichen>
2. …

## Ergebnis / Result
Kurzantwort: <was der Nutzer jetzt sieht/erreicht hat>.

## Troubleshooting   (optional)

## Verwandt / Related
```

### B2. reference (DITA reference — Tabellen/Feldkataloge, Maps)
```markdown
# <Was nachgeschlagen wird> (<EN>)
Kurzantwort: <was diese Reference auflistet>.

## <Feldgruppe als Frage> (z.B. "Welche Felder hat Seite/Command X?")
Kurzantwort: <…>.
| Feld / Field | Erlaubte Werte | Default | Wirkung / Effect |
|---|---|---|---|
| … | … | … | … |

## Verwandt / Related
```

### B3. concept (DITA concept / Diátaxis explanation — kein Klickpfad!)
```markdown
# Was ist <X> / warum? (What is <X> / why?)
Kurzantwort: <ein-Satz-Definition>.

## <Unterfrage> (z.B. "Warum <X> nutzen?")
Kurzantwort: <…>.
<Prosa, bleibt auf EINEM Level — kein Abdriften in Schritte. Für Schritte → Link zum how-to.>

## Verwandt / Related
```

### B4. troubleshooting (KCS Issue/Environment/Cause/Resolution)
```markdown
# <Symptom als Nutzer-Frage> (<EN symptom>)
Kurzantwort: <häufigste Ursache + Fix in einem Satz>.

## Symptom / Issue
## Gilt für / Environment
Kurzantwort: <device + version + Bedingung>.

## Ursache / Cause
## Lösung / Resolution
1. <verifizierter Schritt> …

## Verwandt / Related
```

### B5. faq (kurze Einzelfrage)
```markdown
# <Genau eine Frage> (<EN>)
Kurzantwort: <1–3 Sätze, vollständige Antwort, verlinkt das Detail-how-to>.

## Verwandt / Related
```

### B6. site-state (datierter Live-Stand, instanzspezifisch)
```markdown
# <Instanz/Umgebung> — <was> (Stand: <verified_at>)
Kurzantwort: <der aktuelle Stand in einem Satz>. Generische Anleitung: siehe `canonical_ref`.

## Aktueller Stand / Current state
| Objekt | Wert | … |
|---|---|---|
NUR das Instanz-Delta. KEINE wiederholte Prozedur — die steht im verlinkten how-to.

## Verwandt / Related
```

---

## C. Worked Example (vollständig nach Standard, neutrale Domäne)

Beispieldomäne: KB für ein fiktives Open-Source-CLI-Tool **„Acme CLI"** (Software-Tool-KB).
Datei: `content/own-findings/howto-acme-config-init.md`

```markdown
---
slug: howto-acme-config-init
title: "Acme CLI Konfigurationsdatei anlegen (initialize acme config file)"
article_type: how-to
source_type: own-findings
category: configuration
scope: general
info_type: procedure
summary: "Mit `acme init` im Projektverzeichnis eine acme.toml anlegen, dann unter [profile] den API-Endpoint und das Token-Env setzen, mit `acme config validate` prüfen. Gilt für Acme CLI 3.x."
questions:
  - "Wie lege ich eine Acme-Konfigurationsdatei an?"
  - "Wo trage ich den API-Endpoint für die Acme CLI ein?"
  - "acme.toml erstellen und Profil konfigurieren"
  - "How do I initialize an acme config file?"
  - "How do I set the API endpoint in the Acme CLI?"
  - "Wie validiere ich meine Acme-Konfiguration?"
aliases:
  - "acme.toml"
  - "acme init"
  - "acme config validate"
  - "config file"
  - "Konfigurationsdatei"
  - "[profile]"
applies_to:
  device: "Acme CLI"
  version: "3.12"
status: verified
verified_at: 2026-05-30
owner: "platform-team"
review_cadence: on-major-update
related:
  - concept-was-ist-acme-profile
  - howto-acme-token-rotieren
  - sitestate-prod-acme-config
canonical_ref: ""
language: mixed
last_updated: 2026-05-30
ui_paths:
  - "acme init → acme.toml → [profile] endpoint/token_env → acme config validate"
---

# Acme CLI Konfigurationsdatei anlegen (initialize acme config file)
Kurzantwort: Im Projektverzeichnis **`acme init`** ausführen, in der erzeugten **`acme.toml`** unter **`[profile]`** `endpoint` und `token_env` setzen, dann **`acme config validate`** laufen lassen.

## Beantwortet / Questions this answers
- Wie lege ich eine Acme-Konfigurationsdatei an? / How do I initialize an acme config file?
- Wo trage ich den API-Endpoint ein? / Where do I set the API endpoint?
- Wie validiere ich die Konfiguration? / How do I validate the config?

## Voraussetzungen / Prerequisites
Kurzantwort: Acme CLI 3.x installiert (`acme --version` zeigt 3.12), Schreibrechte im Projektverzeichnis.

## Kontext / When to use
Kurzantwort: Beim ersten Setup eines Projekts oder wenn du ein neues Profil (z. B. staging) anlegen willst.

## Schritte / Steps
Kurzantwort: `acme init` → `[profile]` mit `endpoint` + `token_env` füllen → `acme config validate`.
1. Wechsle ins Projektverzeichnis und führe **`acme init`** aus — es erzeugt **`acme.toml`**.
2. Öffne **`acme.toml`** und finde den Abschnitt **`[profile]`**.
3. Setze **`endpoint = "https://api.example.com"`** (deinen API-Endpoint).
4. Setze **`token_env = "ACME_TOKEN"`** — den Namen der Env-Var, die das Token hält (nie das Token selbst in die Datei).
5. Speichere und führe **`acme config validate`** aus.

## Ergebnis / Result
Kurzantwort: `acme config validate` meldet `OK`; die CLI nutzt nun dieses Profil. Token rotieren → siehe `howto-acme-token-rotieren`.

## Troubleshooting
Kurzantwort: Meldet validate `unknown endpoint scheme`, fehlt das `https://`-Präfix; meldet es `token_env not set`, ist die Env-Var im Shell-Profil nicht exportiert.

## Verwandt / Related
- `concept-was-ist-acme-profile` — Was ist ein Acme-Profil / warum?
- `howto-acme-token-rotieren` — Acme-Token rotieren
- `sitestate-prod-acme-config` — prod: aktive Acme-Config (Live-Stand)
```

---

## D. Transformations-Checkliste: Raw-Doc → Standard-Artikel

Pro Quell-Doc abarbeiten:

1. **Markdown reparieren** (zuerst!): kaputte H1/H2/H3 fixen, Nav/Cookie-/„was this
   helpful?"-Boilerplate strippen, CLI/Config in Code-Fences. Garbage-Headings zerstören
   `heading_path` + 'A'-Gewicht.
2. **Diátaxis-Typ bestimmen** → setze genau einen `article_type`.
3. **Splitten falls gemischt:** mischt das Doc Theorie + Prozedur + Live-State → in 2–3
   atomare Artikel teilen (concept / how-to / site-state), die sich via `related`/
   `canonical_ref` verlinken.
4. **Single-Source:** wiederholte Prozeduren entfernen; site-state referenziert das
   generische how-to statt es zu kopieren.
5. **Titel umschreiben** in Nutzer-Frage/Imperativ, DE+EN-Keywords in ersten 60 Zeichen.
6. **`summary`** (≤50 Wörter, beantwortet Titel) + **`questions`** (3–8, DE+EN) schreiben.
   Optional via günstigem LLM generieren — **nur Fragen, keine neuen Fakten/Pfade**.
7. **Headings frageförmig + bilingual** umschreiben (keine bare Nouns / „Schritt 2").
8. **Answer-First:** `Kurzantwort:` als erste Zeile pro H1 und jeder H2.
9. **`applies_to{device,version}`** eintragen; Pfade/Befehle **gegen die genannte Version
   verifizieren** → `status: verified` + `verified_at`. Unverifiziert →
   `status: unverified-reprint`.
10. **`aliases`** (verbatim Strings + DE↔EN-Synonyme) füllen; `tags` gegen deine
    `tag-registry` prüfen (3–7, lowercase-hyphenated).
11. **`related`-slugs** setzen + auf dangling prüfen.
12. **Frontmatter validieren:** alle REQUIRED da, Enums gültig (`category`/`source_type`
    gegen deine Vokabulare), `language` korrekt.

---

## E. Chunking-Guidance (wie Headings → retrievbare Chunks werden)

- **Split-Grenzen:** H1/H2/H3. Jede Sektion = ein Chunk, sofern unter Token-Budget.
- **Ziel-Sektionsgröße:** **~300–450 Token Body** (≈1200–1800 Zeichen). So bleibt
  `Context-Prefix (50–100 Token) + Body` unter e5's **512-Token-Decke**. Ein größerer
  Sekundär-Split (>~1800 Zeichen ≈ >512 Token) lässt den Tail un-embedded → **Cap auf
  ~1800 Zeichen**, ~240-Zeichen-Overlap behalten. (So konfiguriert in `assets/ingest.py`:
  `BODY_MAX_CHARS`/`BODY_OVERLAP`.)
- **Eine Sektion = eine Idee** (Information Mapping). Deckt eine H2 zwei Ideen ab → in zwei
  H2 splitten, damit der Splitter zwei saubere Single-Idea-Chunks erzeugt statt einer
  gemittelten Embedding.
- **Prozedur zusammenhalten:** eine nummerierte Schrittliste NICHT mitten durchschneiden,
  solange sie ins Budget passt — sonst geht „do these together" verloren.
- **Context-Prepend (deterministisch, kein LLM):** vor dem Embedding jedem Chunk voranstellen:
  `passage: [<title> · <category> · <applies_to.device> · <applies_to.version> · Abschnitt: <heading_path>] <chunk_body>`
  Embedded wird Prefix+Body; **gespeichert/zurückgegeben** wird der **rohe** `chunk_body`.
- **e5-Prefix-Vertrag:** Body-Chunks `passage:`, User-Query + frageförmige Rows
  (`questions`, HyPE) `query:`. Alle Vektoren **L2-normalisieren**.
- **Multi-Vector pro Artikel:** zusätzlich zu Body-Chunks je eine Row für `summary`
  (`passage:`) und je `questions`-Eintrag (`query:`), alle auf `slug` gekeyt;
  nach RRF den Parent-Artikel **deduplizieren**.
- **tsvector-Gewichte:** `heading_path` + `questions` + Titel = **'A'**; Body + `aliases`
  + `tags` = **'B'**. RRF k=60.
