# Prompt-Rezepte für ChatGPT-Image-Restyle

Sammlung von erprobten Prompt-Patterns und Bias-Mitigation-Strategien. Lessons aus Live-Runs (HF #32, #33).

## Default-Prompt (Photo-Modus)

```
{Refs-Label} sind unser visueller Stil. Generiere ein neues Bild im gleichen Stil, das dasselbe Subjekt wie {Target-Label} zeigt — alle Hauptbestandteile drauf, aber mit einer natürlichen, leicht variierten Anordnung (nicht 1:1). Stil/Beleuchtung/Komposition folgen den Referenz-Bildern.{Diet-Hint}{Main-Hint}{Preserve-Hint}
```

`{Refs-Label}` = "Die ersten drei Bilder" / "Die ersten zwei Bilder" / "Das erste Bild" je nach Anzahl.
`{Target-Label}` = "das vierte Bild" / "das dritte Bild" / etc.

## Recipe-Card-Modus

Wenn das Target eine fotografierte gedruckte Karte ist (HelloFresh-Wochenbox, Restaurant-Menü-Karte etc.):

```
{Refs-Label} sind unser visueller Stil. {Target-Label} ist eine abfotografierte Rezeptkarte mit Foto des fertigen Gerichts. Generiere ein neues Bild im Stil der ersten, das das Gericht von der Karte zeigt — alle Hauptzutaten drauf, aber mit einer natürlichen, leicht variierten Anordnung (nicht 1:1). Ignoriere das Karten-Layout, den Text und den Karten-Hintergrund.{Diet-Hint}{Main-Hint}{Preserve-Hint}
```

## Menu-Shot-Modus

Wenn das Target ein Foto/Snapshot ist, aber das gewünschte Subjekt darin isoliert werden soll (z.B. Avatar aus Selfie):

```
{Refs-Label} sind unser visueller Stil. {Target-Label} ist ein Foto/Snapshot des Subjekts in seinem aktuellen Kontext. Generiere ein neues Bild im Stil der ersten, das das Subjekt isoliert + im Anker-Stil zeigt — natürliche Variation, kein 1:1-Copy.{Diet-Hint}{Main-Hint}{Preserve-Hint}
```

## Retry-Prompt (im selben Chat, nach Mismatch)

```
Bitte nochmal — alle Hauptbestandteile aus {Target-Label} drauf, aber natürlich-variiert (nicht 1:1). Stil exakt wie {Refs-Label}.{Diet-Hint}{Main-Hint}{Preserve-Hint}
```

## Bias-Mitigation: Hints anhängen

### Diet-Hint — gegen Fleisch-Bias bei klassischen Gerichtsnamen

```
⚠ Wichtig: das Subjekt ist VEGAN — KEINE Fleisch-/Hähnchen-/Hack-/Speckwürfel. Auch wenn der Name klassisch nach Fleisch klingt (Stroganoff/Bolognese/Carbonara o.ä.), sind ALLE proteinhaltigen Stücke pflanzlich.
```

**Wann nötig:** Bei vegan-Rezepten mit klassisch-fleischigem Gericht-Namen. Konkret beobachtet:
- „Veganes Stroganoff" → AI machte Hähnchenwürfel statt Portobello/Champignons
- „Vegane Bolognese" → AI machte Hack statt Linsen
- „Vegane Carbonara" → AI machte Speck statt Räuchertofu
- „Vegane Gulasch" / „Vegane Königsberger Klopse" / „Veganer Burger" → ähnlich

Bei eindeutig vegetarischen Gerichtsnamen (Bowl, Curry, Stir-Fry, Pinsa) braucht's diesen Hint normalerweise nicht.

### Main-Subjects-Hint — Hauptbestandteile namentlich

```
Hauptbestandteile: {liste} — müssen klar erkennbar im finalen Bild sein.
```

Eine knappe Aufzählung aus 3–6 Items. Beispiele:
- „Portobello-Pilzscheiben, Champignons, Fusilli, cremige Sauce, Kürbiskerne"
- „Tofu-Würfel in Orangensoße, Jasminreis, Cashews, Frühlingszwiebel"
- „Sauerteig-Pinsa-Boden, Aubergine, Spitzpaprika, Tomatensoße"

Keine Wassermengen, Salz/Pfeffer, „etwas Öl" — nur Dinge, die visuell sichtbar sind.

### Preserve-Hint — Garnitur-Anker erhalten

```
Erhalte unbedingt: {liste} — diese Anker dürfen nicht weggelassen werden.
```

Was sich auf dem Original-Target an visuellen Akzenten sieht und beim Restyle erhalten bleiben muss. Beispiele:
- „Zitronenkeile, Frühlingszwiebel-Topping, Sesam-Streusel"
- „Logo-Position oben links"
- „Tabellen-Layout mit den 3 Spalten"

**Wann besonders wichtig:** Bei Retry-Prompts. Beim ersten Versuch kann ChatGPT die Garnituren weglassen, beim Retry kommen sie ohne expliziten Anker garantiert nicht zurück.

## Komplettes Beispiel (Stroganoff-Retry-Fix vom 2026-05-27)

Default:
```
Die ersten drei Bilder sind unser visueller Stil. Generiere ein neues Bild im gleichen Stil, das dasselbe Subjekt wie das vierte Bild zeigt — alle Hauptbestandteile drauf, aber mit einer natürlichen, leicht variierten Anordnung (nicht 1:1). Stil/Beleuchtung/Komposition folgen den Referenz-Bildern. ⚠ Wichtig: das Subjekt ist VEGAN — KEINE Fleisch-/Hähnchen-/Hack-/Speckwürfel. Auch wenn der Name klassisch nach Fleisch klingt (Stroganoff/Bolognese/Carbonara o.ä.), sind ALLE proteinhaltigen Stücke pflanzlich. Hauptbestandteile: Portobello-Pilzscheiben, halbierte Champignons, Fusilli, cremige Hafer-Sauce, Kürbiskerne — müssen klar erkennbar im finalen Bild sein. Erhalte unbedingt: Zitronenkeile, frische Petersilie — diese Anker dürfen nicht weggelassen werden.
```

Mit Diet+Main+Preserve-Hints zusammen war der erste Versuch direkt korrekt (kein Retry nötig).

## Wenn Tag-Override gebraucht wird

Manchmal ist der default-Prompt zu generisch. Dann via `--prompt "<text>"` komplett überschreiben — die anderen Hints werden ignoriert. Useful für:
- Marken-spezifische Tone-of-Voice
- Sehr spezifische Komposition (z.B. „mache es im 16:9-Format, niemand auf dem Bild")
- Mehrsprachige Prompts
