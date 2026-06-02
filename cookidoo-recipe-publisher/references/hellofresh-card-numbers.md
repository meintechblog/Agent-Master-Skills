# HelloFresh „Karten-Nummer" — zwei verschiedene Größen

HelloFresh hat **zwei Nummerierungen**, die beide gerne mit `#NN` notiert werden — aber sie sind **nicht dasselbe**:

| Konzept | Wo zu finden | Wertebereich | Stabilität |
|---|---|---|---|
| **Wochenbox-Position** (`Karte #NN` auf dem Paket) | Aufdruck auf den Lieferpaketen + Karten | typisch 1–8 pro Woche | rotiert pro Woche |
| **Release-Index** (`R<NN>` in der Image-URL) | Dateiname `HF_Y<YY>_R<NN>_W<NN>` | typisch 1–200+ pro Jahr | stabil pro Rezept innerhalb des Jahres |

**Beide können zufällig übereinstimmen, müssen aber nicht.** Beispiele aus dem Repo (Stand 2026-05-28):

| Recipe | Wochenbox `#NN` | Image-URL `R<NN>` | Match? |
|---|---|---|---|
| Pinsa | #25 | R25 | ja |
| Räuchertofu (KW22) | #25 | R33 (aus Y24) | nein |
| Stroganoff | #33 | R32 | nein |

**Ground Truth für die Cookidoo-Identifikation ist die Wochenbox-Position**, weil das ist, was man auf den HelloFresh-Lieferpaketen lesen kann, wenn man am Thermomix steht und einen Karton aus dem Kühlschrank holt.

## Pattern der Image-URL

`HF_Y<YY>_R<NN>_W<NN>_DE_R<NNNN>-<NN>_Main...`

| Token | Bedeutung |
|---|---|
| `Y<YY>` | Year (Y26 = 2026) |
| `R<NN>` | **Release-Index** innerhalb des Jahres — NICHT zwingend die Karten-Position |
| `W<NN>` | Kalenderwoche der Box |
| `DE` | Land |
| `R<NNNN>-<NN>` | HF-interne Master-Recipe-ID + Variante |

## Extraktion (Approximation)

```bash
# Image-URL ziehen → R-Token extrahieren
curl -s "<hf-recipe-url>" | grep -oE 'HF_Y[0-9]+_R[0-9]+_W[0-9]+' | head -1
# Output: HF_Y26_R25_W19  →  R25 (Release-Index)
```

Diese R-Zahl ist eine **Approximation** der Wochenbox-Position. Bei vielen Rezepten matcht sie — bei manchen nicht.

## Verwendung im Workflow

**Wenn der User ein Karten-Foto mitschickt (Pfad B `--image`):**
Lies die `#NN`-Markierung oben rechts auf der HF-Karte (oder das was auf dem Paket-Aufkleber steht) — das ist ground truth. Setze `HF_NR` darauf.

**Wenn nur eine URL gegeben ist (Pfad B `--url`):**
Nutze den R-Index als Approximation. Setze `HF_NR=<R-Zahl>` und dokumentiere im Commit/Recipe-README, dass die Karten-Position nicht verifiziert ist (kann später korrigiert werden).

**Wenn die R-Zahl offensichtlich nicht plausibel ist** (z. B. R142 — keine Wochenbox hat 142 Karten):
Das ist sicher kein Karten-Match. Behandle als unverifiziert.

## Eintrag im Recipe-README

```
| **Quelle** | HelloFresh Wochenbox, Karte #<NN> (HF_Y<YY>_R<NN>_W<NN>, vegan) |
```

Das `Karte #<NN>` ist die **Wochenbox-Position**, die Klammer in `(HF_Y..R..W..)` ist die volle Image-URL-Provenienz (dort kann das `R<NN>` von `Karte #<NN>` abweichen — das ist ok).

Die `extractCardNumber`-Regex der Webapp matched `#(\d+)` aus der Karten-Position. Year/Week in Klammern hilft bei Duplikaten — wenn zwei Rezepte beide `#25` haben (verschiedene Wochen), unterscheidet die Webapp sie nicht automatisch, aber das ist korrekte Information.

## ⚠️ Duplikate sind erwartet, kein Bug

Die Wochenbox-Position rotiert pro Woche. Zwei verschiedene Rezepte aus verschiedenen Wochen können beide `#25` haben (siehe Pinsa + Räuchertofu im Repo). Das ist kein Bug, sondern Realität — die Karten-Position ist nicht eindeutig pro Rezept, nur pro Wochenbox.

Bei expliziter Verwechslungsgefahr: ältere Karte ohne `#`-Nummer notieren, oder Y+W-Marker im Tipps-Block ergänzen.

Verwandt: project-memory [[project_hf_card_number_vs_url_r]] (gleicher Inhalt aus dem Operator-Perspektive notiert)
