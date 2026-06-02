# KB-Methodik — Prinzipien für eine RAG-optimierte Knowledge-Base

> Status: maßgeblich für das „Warum". Diese Datei definiert die **Prinzipien**, mit
> denen ein Agent eine professionelle, RAG-optimierte KB für seine **eigene
> Domäne** baut. Das mechanisch anwendbare Format (Felder, Skelette) steht in
> `article-standard.md`. Bei Konflikt gewinnt der Standard für Felder/Skeletons,
> diese Datei für das „warum".
>
> **Adapt to your domain:** Jedes Beispiel hier ist ein neutraler Platzhalter
> (`<Topic X>`, generische Software-Tool-/Library-KB). Wo „**Adapt:**" steht,
> musst du die Stelle mit deiner eigenen Taxonomie / deinen eigenen Beispielen
> füllen. Feldnamen und technische Begriffe bleiben Englisch.

## 0. Ziel & Stack (Kontext, der jede Entscheidung treibt)

Eine KB nach dieser Methodik beantwortet **natürlichsprachige Fragen** (oft DE+EN
gemischt) sowohl von **Menschen** als auch von **AI-Agenten** — z. B. „wie konfiguriere
ich X?", „wo stelle ich Setting Y um?". Harte Garantie: **keine halluzinierten
Fakten/Pfade** — nur verifiziertes Wissen wird als verifiziert ausgeliefert.

Der Stack, an dem sich alle Regeln messen (Details: `rag-architecture.md`):

- **PostgreSQL + pgvector**, Embedding = `multilingual-e5-small` (384-dim, braucht
  `query:`/`passage:`-Prefixe).
- **Hybrid-Suche** = semantisch (Cosine, HNSW) + `tsvector`-FTS, fusioniert mit
  **Reciprocal Rank Fusion (k=60)**.
- **Heading-aware Chunking**: Split auf H1/H2/H3, `heading_path` gespeichert und im
  tsvector mit Gewicht **'A'**, Body Gewicht **'B'**. Große Sektionen sekundär bei
  ~1800 Zeichen / 240 Overlap gesplittet (unter e5's 512-Token-Decke).
- **Multi-Vector pro Artikel**: Body-Chunks + eine Summary-Row + eine Row je `question`.

**Adapt:** Notiere deinen konkreten Domänen-Kontext (welche Themen, welche Nutzer,
welche „Wahrheitsquelle" — z. B. ein Tool in Version X, eine Library, ein Produkt).
Trag diesen Kontext als `applies_to.device`/`applies_to.version` in jeden Artikel.

Jede Regel unten existiert, weil sie auf **genau diesem** Stack die Retrieval-Precision
hebt oder die No-Halluzination-Garantie absichert — kein Cargo-Cult.

---

## 1. Artikel-Taxonomie: ein sauberes Set von `article_type`

Wir übernehmen die **Diátaxis**-Vier-Felder-Logik (tutorial / how-to / reference /
explanation) als Master-Klassifikator, ergänzt um die **DITA**-Topic-Typen
(concept/task/reference) für das interne Skelett und um **KCS** für den Support-Charakter.
Daraus destillieren wir ein **bewusst kleines, eindeutiges** Set. Mehr Typen = mehr
Fehlklassifikation; weniger = vermischte Embeddings. Empfohlene Default-Sechs:

| `article_type` | Diátaxis/DITA-Wurzel | Beantwortet | Beispiel-Query (generisch) |
|---|---|---|---|
| **how-to** | how-to / DITA task | „Wie mache ich X?" — verifizierte Schritte, eine Aufgabe | „wie konfiguriere ich Feature X?" |
| **reference** | reference / DITA reference | „Welche Felder/Werte/Pfade gibt es?" — neutrale Tabellen, Feldkataloge, Maps | „welche Settings hat Seite/Command X?" |
| **concept** | explanation / DITA concept | „Was ist X / warum?" — Hintergrund, kein Klick-/Befehlspfad | „was ist X / warum braucht man es?" |
| **troubleshooting** | how-to + KCS Cause | „Symptom → Ursache → Fix" | „Fehler Y tritt auf" |
| **faq** | KCS Issue-shaped | kurze Einzelfrage, 1–3 Sätze, verweist auf how-to/reference | „wie setze ich Z zurück?" |
| **site-state** | DITA reference, instanzspezifisch | „Was ist an Instanz/Umgebung X **aktuell** konfiguriert?" — datierter Live-Stand | „welche Config läuft aktuell in Env X?" |

Bewusst **weggelassen**: `tutorial` (geführtes Lernen) — für eine Operator/Agent-KB
über ein bestehendes System selten gefragt; eine Onboarding-Sequenz schreibt man als
verkettete how-tos. Falls je nötig, ist `tutorial` additiv ergänzbar (Faceted Design,
s. §6) ohne Re-Org.

**Adapt:** Diese sechs sind der empfohlene Default und für die meisten technischen
Domänen ausreichend. Du **darfst** das Set anpassen — aber halte es klein und
eindeutig. Wenn du z. B. `site-state` nicht brauchst (keine instanzspezifischen
Live-Stände), lass es weg statt es zweckzuentfremden.

**Eiserne Regel (Diátaxis):** Ein Artikel = **genau ein** `article_type`. Theorie,
Prozedur und Live-Stand werden NIE im selben Artikel gemischt. Das ist die
höchstwirksame Regel — die gemittelte Embedding eines gemischten Docs rankt für jede
Query nur mittelmäßig.

Optionaler feinerer Klassifikator (**Information Mapping**, Robert Horn): ein
`info_type`-Hint (procedure | process | structure | concept | principle | fact) löst
Diátaxis-Grenzfälle — z. B. ein **principle**-Chunk = eine verifizierte Regel wie
„Wert A muss außerhalb von Bereich B liegen".

---

## 2. Answer-First (Inverted Pyramid)

Die **direkte Antwort/Aktion steht im ersten Satz** — pro Artikel **und pro H2-Sektion**.
Begründung im Stack: Heading-aware Chunking liefert dem Agenten **einen** Chunk isoliert;
ist das der erste Chunk unter einer H2 und beginnt er mit Prosa-Kontext statt Antwort,
ist es ein Retrieval- **und** Agent-Failure. Jeder Abschnitt führt mit einer
`Kurzantwort:`-Zeile (NN/g Inverted Pyramid; Microsoft/Google Doc-Style). Der verifizierte
Pfad/Befehl steht NIE unter Absätzen von Caveats begraben.

---

## 3. Atomicity & Single-Sourcing (DRY)

- **Ein Artikel = eine beantwortbare Frage / eine Aufgabe** (Mozilla SUMO, Zendesk,
  Help Scout, Contiem). Braucht der Titel ein „und" (Feature A + Feature B + Feature C)
  → splitten und cross-linken. Atomare Artikel = saubere Chunk-Grenzen; ein fokussiertes
  Doc rankt in **beiden** RRF-Armen (Cosine **und** tsvector) hoch statt überall
  mittelmäßig.
- **Single-Sourcing (DRY):** Jeder Fakt — insbesondere ein verifizierter Pfad/Befehl —
  lebt an **genau einer** kanonischen Stelle. Andere Artikel **referenzieren** ihn statt
  ihn zu kopieren. Ein verifizierter Pfad, N-fach wiederbenutzt, schlägt N driftende
  Kopien. Das ist zugleich der **Halluzinations-Guard**: keine widersprüchlichen
  Pfadkopien.
- **Nicht über-atomisieren:** atomar = ein vollständiger Zweck, nicht ein Satz. Eine
  Prozedur-Schrittliste bleibt in einem Chunk, wo sie ins Token-Budget passt — sonst
  geht die „do these together"-Bedeutung verloren (Information Mapping).

---

## 4. Wiederverwendbares Wissen vs. instanzspezifischer Live-Stand

Ein häufiges Altlast-Problem: ein Doc mischt generische How-tos mit dem konkreten
Live-Stand einer Instanz/Umgebung. KCS und Single-Sourcing sagen klar: **trennen**.

- Generisches **how-to** (`scope: general`, evergreen): „Feature X einrichten — Tool
  Version Y". Enthält NIE „in Env A ist X auf Wert 42 gesetzt".
- **site-state** (`scope: site:<env>`, datiert): „Env A — aktuelle Config von X".
  Enthält NUR das Instanz-Delta und **verlinkt** das generische how-to (`canonical_ref`),
  ohne die Prozedur zu wiederholen.

Live-Stand ist **datierte Reference**, kein evergreen how-to. Ohne `verified_at` veraltet
er still und wird weiter als aktuell retrievt — das korrodiert Vertrauen und die
No-Halluzination-Garantie.

**Adapt:** „Instanz/Umgebung" ist generisch — bei dir kann das ein Deployment, ein
Kunde, eine Maschine, ein Cluster, ein Standort sein. Definiere deine `scope`-Werte
(`general` + `site:<deine-instanzen>`) selbst. Hast du nur eine globale Wahrheit ohne
instanzspezifische Stände, brauchst du `site-state` nicht.

---

## 5. Retrieval-Optimierung auf Content-Ebene

Kernproblem (E5-Paper, Retrieval-Asymmetrie): User-Queries sind **interrogativ**
(„wie konfiguriere ich X?"), KB-Content ist **deklarativ** („Settings → X → Create").
Beide landen in verschiedenen Embedding-Regionen. Wir schließen die Lücke mehrfach:

1. **e5-Prefixe rigoros (nicht verhandelbar).** Jeder indexierte Body-Chunk =
   `passage: …`, jede User-Query = `query: …`. **Frageförmiger** Text (Titel,
   `questions`-Liste, FAQ-Q-Zeilen, HyPE-Fragen) wird als `query:` embedded — gleiche
   Form/gleicher Prefix wie die einkommende Query (E5-Modellkarten). Fehlender/falscher
   Prefix halbiert die Qualität **lautlos** — gilt auch für deutschen/mehrsprachigen Text.
   Außerdem: alle e5-Vektoren **L2-normalisieren**, sonst ist die pgvector-Cosine
   bedeutungslos.
2. **„Questions this answers"-Metadaten (höchster ROI).** Jeder Artikel trägt 3–8
   verbatim natürlichsprachige Fragen in **DE und EN** („wie konfiguriere ich X?", „how
   do I configure X?"). Sie (a) werden je als eigene Vektoren indexiert, die auf den
   Artikel zeigen, und (b) in den tsvector mit Gewicht **'A'** gefaltet. Das überbrückt
   genau die Vokabular-Lücke, die das 384-dim-e5-small nicht immer schließt — und fängt
   das echte Register der Nutzer.
3. **HyPE (index-time hypothetical questions).** Beim Ingest pro Chunk 6–10 hypothetische
   Fragen generieren — **über einen günstigen LLM (z. B. lokaler Gateway, haiku-Klasse)**,
   NICHT mit dem teuren Session-Modell (Delegations-Policy). Question→Question-Matching
   ist weit enger als Question→Statement. **Nur Fragen speichern, nie generierte
   Antworten** — sonst kommen halluzinierte Pfade rein. Prompt-Constraint: „questions
   only, no new facts, no menu paths", gefüttert nur mit verifiziertem Chunk-Text.
4. **Contextual Retrieval (Anthropic; ~35 % weniger Failures allein, ~49 % mit
   contextual BM25, ~67 % mit Rerank).** Vor dem Embedding wird jedem Chunk ein
   50–100-Token-Kontext **physisch vorangestellt**, deterministisch aus Frontmatter +
   `heading_path` abgeleitet — z. B. `passage: [<title> · <category> · <device> ·
   <version> · Abschnitt: <heading_path>] <chunk>`. Embedded wird Kontext+Chunk;
   **zurückgegeben/gespeichert** als Payload wird der **rohe** Chunk. Das ist die
   operative Form von EPPO „establishes context" für Sub-Artikel-Chunks und der größte
   Zero-LLM-Recall-Gewinn für Sekundär-Splits, die ihr Heading verlieren.
5. **Self-contained Chunks (EPPO „Every Page Is Page One").** Retrieval liefert EINEN
   Chunk ohne umgebende Narrative — der Chunk **ist** die Page-One. Daraus folgen harte
   Verbote: keine relationalen Schritte („continue from above", „klick Next vom vorigen
   Screen", „wie oben beschrieben"); kein H1-only-Scope (Modell/Version muss im Body
   re-stated **und** per Context-Prepend re-injiziert werden); ein Chunk bleibt auf
   **einem** Level (entweder how-to ODER explanation, kein Mid-Section-Drift).
6. **Frageförmige Headings (Labeling + Consistency, Information Mapping).** H2/H3 sind
   beschreibende, keyword-tragende, **bilinguale** Frage-/Task-Phrasen: „Feature X
   aktivieren (enable feature X)" statt „Configuration"/„Schritt 2". Das Heading trägt
   die **'A'**-FTS-Last und ankert die Chunk-Embedding.
7. **Lexikalische Anker für den FTS-Arm.** Weil wir tsvector fusionieren, MÜSSEN Artikel
   die exakten Strings verbatim nennen: Modell-/Versions-Strings, exakte
   Menü-Breadcrumbs, CLI-Tokens, Ports, IDs, Error-Messages — plus eine **Synonym/Alias-
   Map** (DE↔EN-Synonyme und Term-Varianten). e5 bridged Cross-Lingual-Semantik, aber
   BM25/tsvector ist lexikalisch und braucht die Alias-Hilfe, um sein RRF-Gewicht zu
   ziehen. **Adapt:** sammle die echten verbatim Strings deiner Domäne.
8. **512-Token-Decke respektieren.** e5-small hat eine **512-Token**-Eingabegrenze.
   Body-Cap auf **~400–450 Token (~1800 Zeichen)** halten, damit Context-Prefix + Body
   unter 512 bleibt; ~240-Zeichen-Overlap behalten. Ein größerer Split lässt den Tail
   langer Sektionen faktisch un-embedded.
9. **Cross-Encoder-Rerank (optional, billigster Hebel nach Content).** Top ~20 RRF-Hits
   in einen kleinen multilingualen Reranker (z. B. `bge-reranker-v2-m3`, DE+EN), Top 3–5
   behalten. Anthropic: Failure-Reduction 49 %→67 %. Stage-1-Kandidatenfenster ~20 (nicht
   3–5), Reranker entscheidet die finale Ordnung. (Swap-in-Punkt, s. `rag-architecture.md`.)
10. **RRF-Biasing.** k=60 behalten, Tendenz semantisch (~80/20 ist sane Start). Per
    Metadaten biasen: für prozedurale Queries `article_type=how-to` bevorzugen, für
    Fakten `reference`; stale `site-state` down-ranken; `verified`/offizielle Quellen
    up-ranken, damit verifizierte Pfade Ties gewinnen (operationalisiert „no hallucinated
    paths").

---

## 6. Taxonomie & Metadaten (Faceted Classification + Frontmatter-as-Schema)

Statt **eines** tiefen Baums beschreiben wir jeden Artikel über **orthogonale Facetten**
(Ranganathan-Facet-Theory; Hedden). `category` bleibt die **eine** Topic-Facette (genau
eine pro Artikel). Daneben eigene Facetten: `article_type`, `scope` (general vs site:*),
`status` (verified/…). Facetten werden zu **Pre-Filter-Spalten** in pgvector
(`WHERE category=… AND article_type=…`) und schrumpfen das ANN-Candidate-Set vor der
Cosine-Sortierung.

- **Controlled vocabulary** für Facetten = strikt geschlossene Enums, beim Ingest
  validiert (unbekannter Wert → **laut** failen). `tags` = leichte, **registrierte**
  Folksonomy (3–7, lowercase-hyphenated) für den Long-Tail (Produkt-/Feature-Namen,
  verbatim Error/UI-Strings) → eine `tag-registry`-Datei, Dedup-Check gegen
  Synonym-Sprawl (`foo` vs `foos` vs `foobar`).
- **Frontmatter-as-Schema:** jeder Artikel ist ein typisierter, abfragbarer Record. Schema
  EINMAL definieren, **uniform** anwenden — inkonsistente Feldnamen brechen jede Query
  still. `questions`/`summary` werden NICHT als inerter YAML liegengelassen, sondern in
  den embedded Text **und** tsvector gefaltet.

**Adapt — die zwei Facetten, die DU definieren musst:**
- **`category`** = deine **eine** geschlossene Topic-Liste (z. B. 8–12 Werte für die
  Hauptthemen deiner Domäne). Beispiel für eine Software-Tool-KB: `installation`,
  `configuration`, `api`, `cli`, `auth`, `integrations`, `troubleshooting`, `concepts`.
- **`source_type`** = deine geschlossene Herkunfts-Liste (z. B. `official-docs`,
  `community-forum`, `github`, `manual-pdf`, `own-findings`, `blog`). Diese Liste muss
  **identisch** in `assets/schema.sql` und `assets/ingest.py` stehen.

**Achtung (Tiny-Corpus):** Bei kleinem Bestand Facetten-Filter für den Operator-Pfad
**weich/optional** halten — gestapelte strikte Filter (category AND type AND status AND
version) liefern leicht **null** Treffer. Strikt nur im Agent-„verified-only"-Lane.

Vollständige Feldliste + Typen: `article-standard.md` §A.

---

## 7. Content-Lifecycle & Verifikation (KCS Article State + Governance)

Artikel tragen einen expliziten **Confidence-/Status**-Zustand (KCS v6):
`draft | verified | unverified-reprint | deprecated | archived`. Status ist **Metadaten,
keine Löschung** — der Retrieval-Layer **boostet** `verified` und **down-ranked/labelt**
unverified; der Agent kann eine Antwort aus einem Reprint mit niedrigerer Confidence
einleiten.

- **Verified-Path-Disziplin:** jeder Navigations-/Befehlsstring trägt die Version, gegen
  die er verifiziert wurde (`applies_to.version`) + `verified_at`-Datum. Unverifizierte
  Reprints → `status: unverified-reprint`, damit der Agent hedged.
- **Governance-Cadence:** ein benannter `owner` (eine Person, kein „Team"). `review_cadence`:
  fundamentale/konzeptionelle Artikel selten; **volatile pfad-/befehlslastige Artikel bei
  jedem Major-Update re-verifizieren**. UIs/APIs ändern sich zwischen Releases — stale
  Pfade sind confident-wrong und vergiften Vertrauen.

---

## 8. Content-Audit + Gap-Analyse (Vorgehen)

Vor jedem Restrukturieren: **quantitatives Inventory, dann qualitativer Audit**, gebunden
auf **eine `category` zur Zeit** (Scope-Creep-Schutz; ~85 % der KBs tragen Duplikate, also
ist Dedup First-Class).

**Schritt 1 — Inventory (ein Script-Pass über alle Docs).** Pro Doc eine Zeile:
`id/slug, title, source_type, category, vermuteter article_type, scope, language,
last_updated, length(chars), source_url, hat_frontmatter?`.

**Schritt 2 — Qualitativ taggen.** Jede Zeile eine Aktion:
`keep | normalize | split | merge | retire`.
- `split` ← Doc mischt Diátaxis-Typen oder general+site-state.
- `normalize` ← mangled-Markdown-Reprint (kaputte H1/H2 zerstören `heading_path` →
  'A'-Gewicht weg). Markdown-Reparatur ist **Voraussetzung**, kein Nice-to-have: Headings
  fixen, Nav/Boilerplate/„was this helpful?" strippen, CLI/Config in Code-Fences, dann
  ins typisierte Skelett.
- `merge` ← echte Duplikate (eine kanonische Quelle behalten, Rest referenziert sie).
- `retire` ← superseded → `status: archived` (nicht löschen).

**Schritt 3 — Gap-Analyse gegen reale Queries.** Eval-Set aus echten Fragen bauen
(DE+EN, Novice+Expert, Symptom+Solution) mit erwartetem Zielartikel. Fehlende Antworten =
Backlog für neue how-tos. **Recall@k / MRR vor und nach** den Question-Lanes messen — die
HyPE/Hypothetical-Question-Literatur ist einhellig: per-Corpus evaluieren, nicht annehmen.

**Adapt — Reihenfolge:** beginne mit den 1–2 `category`-Werten, die deine häufigsten
realen Queries treiben, dann die übrigen Kategorien.

---

## 9. Zitierte Frameworks (Mapping)

- **Diátaxis** (diataxis.fr) — Master-Klassifikator `article_type`; Typen nicht mischen.
- **DITA topic typing** (OASIS; Heretto) — internes Skelett (task/concept/reference).
- **Information Mapping** (Robert Horn) — Chunk-Disziplin: ein Chunk = eine Idee; Labeling
  → 'A'-gewichtetes Heading; Consistency → DE/EN-Term-Standardisierung.
- **EPPO** (Mark Baker, „Every Page Is Page One") — self-contained Chunks; establishes
  context; one level; rich links.
- **KCS v6** (Consortium for Service Innovation) — Issue/Environment/Resolution/Cause-
  Skelett; Article-State/Confidence.
- **Inverted Pyramid** (NN/g) — Answer-First pro Sektion.
- **Contextual Retrieval** (Anthropic) — Context-Prepend pro Chunk.
- **HyPE / HyDE / QuIM-RAG** — index-time hypothetical questions, Question→Question.
- **Faceted Classification** (Ranganathan / Hedden) + **Frontmatter-as-Schema** — Metadaten.
- **E5 / multilingual-e5** (Wang et al.) — `query:`/`passage:`-Prefix-Vertrag, L2-Norm.
- **Reciprocal Rank Fusion** (Cormack et al.) — RRF k=60 Fusion zweier Ranglisten.

---

## 10. Pitfalls (die teuersten zuerst)

1. Diátaxis-Typen in einem Artikel mischen (Theorie+Schritte+Site-State) → gemittelte
   Embedding rankt überall mittelmäßig. **Der häufigste Altlast-Fehler.**
2. Relationale Schritte („continue from above") brechen unter RAG katastrophal — der
   mittlere Chunk hat kein „above".
3. H1-only-Scope: nach dem Split verlieren tiefe Chunks Modell/Version → Agent antwortet
   out-of-scope. Scope pro Body **und** per Context-Prepend re-injizieren.
4. Zu großer Sekundär-Split übersteigt 512 Token → Tail un-embedded. Cap senken.
5. Mangled-Markdown-Reprints → garbage `heading_path` → 'A'-Boost weg. Vor Ingest fixen.
6. e5 `query:`/`passage:`-Asymmetrie ignorieren/gleich anwenden → lautlos halbierte Qualität.
7. e5-Vektoren nicht normalisieren → Cosine bedeutungslos.
8. Generierte **Antworten** statt nur Fragen embedden → so kommen halluzinierte Pfade rein.
9. Bilingual-Drift (Term/EN-Synonym) ohne Synonym-Map → FTS-Arm underperformt.
10. Über-Filtern auf kleinem Corpus → null Treffer. Operator-Facetten weich halten.
