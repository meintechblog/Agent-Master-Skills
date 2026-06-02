# Native-Style Rules — wie ein Step wirklich aussehen muss

> **Komplett-Rewrite 2026-05-28** nach Analyse von ~12 echten Cookidoo/Vorwerk-Rezepten
> in ihrer **per-Schritt-Segmentierung** (nicht der zusammengefassten Web-Prosa).
> Die alte Regel „250-550 Zeichen pro Step, median 5 Steps" war **falsch** und hat
> Mega-Steps produziert, durch die man am Thermomix nicht durchkommt. Sie ist hiermit
> ungültig. Belege: `research/native-step-corpus.md`.

## Das mentale Modell: der Anwender steht am Thermomix

Er ist müde, will schnell kochen, hat **null Lust auf Fließtext**. Jeder Step auf dem
Display muss in **2 Sekunden** drei Fragen beantworten:

1. **Was kommt jetzt rein** — Zutaten + exakte Menge/Gewicht (das sind die fett-Chips zum Abhaken)
2. **Muss ich davor was schnibbeln** — falls ja, in einem kurzen Halbsatz
3. **Was macht die Maschine / Pfanne / Ofen** — der Koch-Befehl-Chip oder Zeit/Hitze

Mehr nicht. Wenn ein Step mehr will, ist es zu viel — splitten.

## Die 5 Kern-Regeln (in dieser Reihenfolge wichtig)

### 1. EINE Operation pro Step
Ein Step dreht sich um **genau eine** Aktion: ein Mixtopf-Chip ODER eine Pfannen-Aktion
ODER eine Ofen-Aktion ODER ein Anrichte-Schritt. **Nie zwei Chips in einem Step.**
„Kräuter hacken UND Oliven hacken UND Dressing rühren" = drei Steps, nicht einer.

### 2. Länge ist KEINE harte Grenze — „eine aktive Operation pro Step" ist das Gesetz
> **Korrigiert 2026-05-29** nach frischem Dump von 12 echten Top-Rezepten der
> Cookidoo-Startseite (`research/native-top-recipes-2026-05-29.md`). Die alte Regel
> „40-130, hart bei 180" war **zu streng**: native Rezepte haben routinemäßig Steps
> von 200-380 Zeichen, und das sind keine Fehler.

Der eigentliche Maßstab ist **nicht die Zeichenzahl**, sondern: **macht der Step
genau eine aktive Sache, die der Anwender am Gerät ausführt?** (ein Mixtopf-Chip ODER
eine Pfannen-Aktion ODER ein Anricht-Schritt — plus die natürliche Nacharbeit dazu wie
`umfüllen`, `Mixtopf spülen`, `mit dem Spatel nach unten schieben`).

Ein Step **darf lang sein** (>180, bis ~380), wenn die Länge aus einer von zwei
legitimen Quellen kommt:

1. **Laufender Chip + Parallel-Handarbeit** — ein Gar-/Koch-Chip läuft (z. B.
   `20 Min./Varoma/Stufe 1`), und per `In dieser Zeit …` / `Währenddessen …` faltet
   man manuelle Prep oder eine Pfannen-Aktion hinein. Der Anwender hat ja **Leerlauf**,
   während die Maschine arbeitet — hier ist Text völlig OK. *(Native-Beleg: Quinoasalat
   Step 3, 305c: „… 20 Min. kochen. In dieser Zeit Cherry-Tomaten halbieren, Hähnchen
   anbraten …")*
2. **Finaler Anricht-/Servier-Step** — der letzte Step bündelt fast immer letzte
   Mixtopf-Aktion + Anrichten + `servieren` und ist nativ 200-380c lang. Völlig normal.

| Situation | Bewertung |
|---|---|
| Eine aktive Operation, kurz (40-180c) | ✓ ideal |
| Laufender Chip + `In dieser Zeit` Parallelarbeit (auch 200-380c) | ✓ nativ |
| Finaler Anricht-/Servier-Step (auch 200-380c) | ✓ nativ |
| **Zwei aktive Maschinen-/Pfannen-Operationen hintereinander** in einem Step | ✗ splitten |
| Mehrere **sequenzielle aktive Handgriffe** ohne laufende Maschine zusammengequetscht | ✗ splitten |

Der Räuchertofu-Graus war Fall 4+5: aktive Operationen aneinandergereiht, ohne dass
eine Maschine die Wartezeit füllt. *Das* ist unbenutzbar — nicht die reine Länge.

### 3. Step-Zahl = Anzahl der aktiven Operationen, NICHT Zutatenzahl
Es gibt **keine** Ziel-Step-Zahl. Zähle die echten aktiven Operationen (jeder
Mixtopf-Chip, jede eigenständige Pfannen-/Ofen-Phase, das Anrichten) — das ist die
Step-Zahl. **Frische Ground-Truth:** native Top-Rezepte liegen bei **3-7 Steps**
(Median ~4 bei einfachen Gerichten, bis ~7 bei Mehrkomponenten). Unsere
Usability-optimierte Variante darf etwas granularer sein (Zutaten inkrementell, Anrichten
als eigener Step) und landet typisch bei **6-12 Steps** — aber **nie künstlich
zerhacken** (einen Chip von seinem `umfüllen`/`spülen` trennen bringt nichts) und **nie
zusammenquetschen** um eine Zahl zu treffen.

### 4. Zutaten kommen INKREMENTELL rein
Nicht 6 Zutaten in einen Step kippen. Jede Zutat erscheint **mit ihrer Menge in genau
dem Step, in dem sie verwendet wird** — in der Reihenfolge, in der die Maschine sie
braucht. So ist jeder Step gleichzeitig die „jetzt-einwiegen"-Liste.

```
✗ Step: 2 Gurken, 2 Zitronen, 50 g Mayo, 100 g Sojaprodukt, Salz, Pfeffer ... (alles auf einmal)
✓ Step A: Gurke zerkleinern.  Step B: Mayo + Sojaprodukt + Salz zugeben, vermengen.
```

### 5. Die MASCHINE macht die Vorbereitung
Zwiebel, Knoblauch, Kräuter, Oliven, Pilze, Nüsse → im **Mixtopf zerkleinern/hacken**
mit einem Chip, NICHT „fein würfeln" als Handarbeit beschreiben. Das ist der ganze Sinn
des Geräts und der Grund, warum native Rezepte so kurz sind.

**Manuelle Prep nur**, wenn die Maschine es nicht kann (Tofu in Scheiben hobeln, etwas
das in Stücken bleiben muss, Ofen-Kram) — und dann **in einen laufenden Maschinen-Step
falten** mit `Währenddessen ...` / `In der Zwischenzeit ...`.

## Step-Grammatik (das Bauschema)

```
<Zutat A, Zutat B (+ Mengen)> in den Mixtopf geben[/zugeben], <CHIP> <verb>[, <aufräumen>].
```
optional davor/danach: `Währenddessen <kurze manuelle Prep>.`

Echte native Beispiele (so kurz!):
- `Zwiebeln und Knoblauch in den Mixtopf geben, 3 Sek./Stufe 5 zerkleinern und mit dem Spatel nach unten schieben.`
- `Olivenöl zugeben und 5 Min./120 °C/Stufe 1 dünsten. Währenddessen Champignons in 3 mm Scheiben schneiden.`
- `Weißwein zugeben und 3 Min./120 °C/Stufe 1 reduzieren.`
- `Gyros zugeben und anbraten.`

## Native Verb-/Phrasen-Vokabular

Diese Bausteine kennt der Thermomix + Cookidoo:

| Zweck | Native Phrase |
|---|---|
| Zutat einfüllen (1. mal) | **in den Mixtopf geben** |
| Zutat nachlegen | **zugeben** |
| in Gareinsatz/Varoma | **einwiegen** |
| Gareinsatz reinhängen | **einhängen** |
| Varoma drauf/runter | **aufsetzen / absetzen** |
| Topf-Rand säubern | **mit dem Spatel nach unten schieben** |
| Inhalt rausnehmen | **umfüllen** / **mithilfe des Spatels herausnehmen** |
| Topf reinigen | **Mixtopf spülen** |
| parallele Handarbeit | **Währenddessen … / In der Zwischenzeit …** |
| zur Seite | **beiseitestellen** |
| Schluss | **… servieren** (NIE „Guten Appetit!") |

## Mehrkomponenten-Gerichte (Salat + Dip + Hauptkomponente …)

Der „Eigene Rezepte"-Editor hat **keine** Sektionen/Gruppen — alles ist eine flache
Step- und Zutatenliste. Deshalb:

- Komponenten so **ordnen, dass der Mixtopf fließt**: erst das, was trocken/sauber
  gemixt wird (Kräuter, Topping), dann Dips/Soßen, dazwischen `umfüllen` + `Mixtopf spülen`.
- Den Mixtopf **wiederverwenden** statt für jede Komponente neu anzusetzen.
- Wenn es der Klarheit hilft, einen Step mit einem **1-Wort-Cue** beginnen
  (`Fürs Zaziki: …`). Sparsam einsetzen, nicht jeden Step.
- Ofen-Komponente (z. B. Kartoffeln) als **ersten Step** (läuft 25 Min. im Hintergrund),
  der Rest per `In der Zwischenzeit` parallel.

## Skalierung & Mengen — Bruchteil-Falle

**Lektion Räuchertofu #25:** HF-Original (2 P) sagte „1 Gurke … die Hälfte raspeln …
restliche Hälfte würfeln". Beim Skalieren auf 4 P (= 2 Gurken) wurde daraus
„die eine Hälfte … die andere Hälfte" → **mehrdeutig** (Hälfte von je einer Gurke? oder
eine ganze von zweien?). Anwender hat's falsch verstanden.

**Regel:** Wenn Skalieren einen „halb X / halb X"-Split auf **ganze Einheiten** hebt,
schreib ganze Einheiten: `1 Gurke raspeln` / `1 Gurke würfeln`. Niemals
„die eine Hälfte / die andere Hälfte" wenn die Menge ≥ 1 ganze Einheit ist.
Mengen immer **konkret** im Step nennen (Gramm/Stück/TL), nie „etwas", „nach Bedarf".

## Zutatenliste (bestätigt durch native Rezepte)

- **Adjektiv nach Komma**: `1 Chilischote, frisch` (nicht „1 frische Chilischote")
- **Modifikator nach Komma**, Verb gehört in den Step: `1 Zitrone, gewachst`
  (das „in Spalten" steht im Step, nicht in der Zutatenzeile)
- **Spezifische Mengen** statt Sammelposten: `2 TL Salz` + `25 g Öl` + `1 Prise Pfeffer`
  als getrennte Zeilen, nie „Salz, Pfeffer, Öl nach Bedarf"
- **Bindestrich-Soßen**: `Sweet-Chili-Soße`, `Teriyakisoße` (kein Leerzeichen)
- **Kein „Stück"**: `2 Karotten`, nicht „2 Stück Karotten"

## Was native Rezepte NIE haben

- Mega-Steps mit 4+ Aktionen
- `Guten Appetit!` als Schluss
- Doppelpunkt-Listen im Step (`Toppings: …`) — Fließtext
- Klammer-Hinweise in der Zutatenliste — gehören in den Step
- HelloFresh-Multiplier-Brackets `[1,5 EL | 2 EL]` — vorab auf eine Portionsgröße runterrechnen
- mehrfaches Nennen derselben Zutat über Steps verteilt ohne neue Menge
