#!/usr/bin/env python3
"""UTF-8-safe slugify for recipe names with umlauts.

Replaces German umlauts with their ASCII equivalents (ä→ae, ö→oe, ü→ue, ß→ss),
then strips remaining non-alphanumerics. Used by the SKILL to derive
recipes/<slug>/ directory names from German recipe titles.

Usage:
  ./_slugify.py "Sweet-Chili-Bowl mit glasierter Aubergine"
  → sweet-chili-bowl-mit-glasierter-aubergine

  ./_slugify.py "Nasi Goreng mit veganen Filetstücken (HelloFresh)"
  → nasi-goreng-mit-veganen-filetstuecken-hellofresh
"""
import sys, re, unicodedata

UMLAUT_MAP = str.maketrans({
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
    "é": "e", "è": "e", "ê": "e",
    "á": "a", "à": "a", "â": "a",
    "ó": "o", "ò": "o", "ô": "o",
    "í": "i", "ì": "i", "î": "i",
    "ú": "u", "ù": "u", "û": "u",
    "ñ": "n", "ç": "c",
})


def slugify(name: str) -> str:
    s = name.translate(UMLAUT_MAP)
    # NFKD-decompose any remaining accented chars, drop combining marks
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: _slugify.py \"<name>\"", file=sys.stderr); sys.exit(64)
    print(slugify(sys.argv[1]))
