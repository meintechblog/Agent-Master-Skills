---
slug: <kebab-case-stable-slug>      # z.B. sitestate-<instanz>-<thema>
title: "<Instanz/Umgebung> — <was> (Stand: <YYYY-MM-DD>)"
article_type: site-state
source_type: <YOUR-SOURCE-TYPE>      # typically own-findings — must be in your ALLOWED_SOURCE_TYPES
category: <YOUR-CATEGORY>            # must be in your ALLOWED_CATEGORIES
scope: site:<deine-instanz>         # z.B. site:prod, site:eu, site:kunde-a
info_type: structure
summary: "<≤50 Wörter: der aktuelle Stand an dieser Instanz in einem Satz. Datiert.>"
questions:
  - "<Welche Config läuft aktuell in <Instanz>? (DE)>"
  - "<What is currently configured in <instance>? (EN)>"
aliases:
  - "<instanzspezifische verbatim Werte>"
applies_to:
  device: "<Tool/Produkt>"
  version: "<Version>"
  site: "<deine-instanz>"           # REQUIRED für site-state
status: verified
verified_at: <YYYY-MM-DD>           # REQUIRED — datiert den Live-Stand
owner: "<Name>"
review_cadence: on-major-update
related:
  - <generic-howto-slug>
canonical_ref: <generic-howto-slug>  # PFLICHT: das generische how-to, das dies referenziert
language: mixed
last_updated: <YYYY-MM-DD>
---

# <Instanz/Umgebung> — <was> (Stand: <YYYY-MM-DD>)
Kurzantwort: <der aktuelle Stand in einem Satz>. Generische Anleitung: siehe `<generic-howto-slug>`.

## Aktueller Stand / Current state
Kurzantwort: <eine-Satz-Zusammenfassung des Deltas>.
| Objekt | Wert | <Spalte> |
|---|---|---|
| <PLACEHOLDER> | <PLACEHOLDER> | <PLACEHOLDER> |
<!-- NUR das Instanz-Delta. KEINE wiederholte Prozedur — die steht im verlinkten how-to. -->

## Verwandt / Related
- `<generic-howto-slug>` — generische Anleitung (Single-Source)
