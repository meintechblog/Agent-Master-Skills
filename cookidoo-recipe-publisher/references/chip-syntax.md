# Chip-Syntax — wie die AI-Annotate-API Koch-Befehle als interaktive Chips erkennt

Cookidoo's `POST /created-recipes/de-DE/annotate/steps` extrahiert Koch-Befehl-Chips aus Plain-Text-Schritten. Die TTS-Erkennung ist **streng formatabhängig** — falsches Format = kein Chip = Plain-Text.

## Pattern

```
<DAUER> <EINHEIT>./<TEMPERATUR>?/<MODIFIKATOR>?/<STUFE>
```

Wobei:
- **DAUER**: Integer oder Range (`18`, `5-6`, `1`, `30`)
- **EINHEIT**: `Min.` oder `Sek.` (immer mit Punkt!)
- **TEMPERATUR** (optional): `Varoma`, oder `<N> °C` (mit Leerzeichen vorm °C, z.B. `100 °C`)
- **MODIFIKATOR** (optional): `Linkslauf` (für Rückwärts-Drehung)
- **STUFE**: `Stufe <N>` (N = 0.5, 1, 1.5, 2, 2.5, ..., 10), `Stufe soft`, `Stufe Teig`

Slash zwischen jedem Segment, KEINE Leerzeichen um den Slash.

## Beispiele die als Chip funktionieren

| Plain-Text im Step | Wird zum Chip | Zweck |
|---|---|---|
| `18 Min./Varoma/Stufe 1` | ✓ | Dampfgaren im Varoma |
| `10 Sek./Stufe 6` | ✓ | Kurz pürieren |
| `6 Min./100 °C/Linkslauf/Stufe 1` | ✓ | Dünsten mit Linkslauf (Anti-Häckseln) |
| `30 Sek./Stufe 4` | ✓ | Vermengen |
| `3 Min./120 °C/Stufe 2` | ✓ | Anbraten/Reduzieren |
| `2 Min./Teig` | ✓ | Teig kneten |

## Beispiele die NICHT funktionieren

| Plain-Text | Problem |
|---|---|
| `18 Min. Varoma Stufe 1` | fehlende `/` Trenner |
| `18 Min Varoma Stufe 1` | fehlender Punkt nach `Min` |
| `18 min./varoma/stufe 1` | lowercase wird teilweise akzeptiert, sicherer ist Capitalization |
| `18min./Varoma/Stufe 1` | fehlendes Leerzeichen nach DAUER |
| `18 Min./Varoma/Stufe 11` | Stufe 11 existiert nicht (max 10) |
| `18 Min./150°C/Stufe 1` | fehlendes Leerzeichen vor °C |

## Erlaubte Temperaturen

```
OFF, 37, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 98,
100, 105, 110, 115, 120, 140, 145, 150, 155, 160, Varoma
```

## Erlaubte Stufen

```
0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6, 6.5, 7, 7.5,
8, 8.5, 9, 9.5, 10, soft, Teig
```

## Mode-Glyphs (kein TTS-Chip, sondern eigener `cr-mode`-Type)

Diese werden NICHT als `<cr-tts>` sondern als `<cr-mode>` annotiert — separate Auto-Detection:

| Plain-Text | Mode |
|---|---|
| `Teig kneten` | Knet-Modus |
| `Pürieren` | Pürierer (kurz) |
| `Anbraten` | Sear-Modus |
| `Dampfgaren` | Varoma-Modus (Standalone) |
| `Warm halten` | Hold-Modus |
| `Reis kochen` | Reis-Programm |

Diese Modus-Wörter im Step → Cookidoo zeigt das passende Icon-Chip.

## Wenn der Annotate-API einen Chip NICHT findet

Mögliche Ursachen:
- Format-Abweichung (siehe Tabelle oben)
- Falsche Stufe (`11`, `13`)
- Falsche Temperatur (`130 °C` ist nicht in der Liste)
- Sonderzeichen `°` als `o`/`grad`/`*` statt `°` (U+00B0)

Workaround: TTS-Modal manuell öffnen (⚡-Icon unter dem Step) und Werte eintragen. ABER: automatisches Annotate ist 10x schneller, lieber das Format anpassen.
