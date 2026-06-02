#!/usr/bin/env python3
"""Audit a proposed recipe for the 9 native-quality rules before pushing to Cookidoo.

Reads a JSON file:
  { "ingredients": ["300 g Basmatireis", ...],
    "steps": ["Limetten in 8 Spalten...", ...] }

Checks:
  1. Per-step ingredient uniqueness (every ingredient max 1x per step) —
     plural/synonym pairs normalized via INGREDIENT_NORMALIZATION so
     `Bohnen` + `Buschbohnen` correctly trip as the same chip.
  2. Cross-step adjacent endings (normalized + lowercased before comparing —
     `beiseitestellen.` and `beiseite stellen.` count as the same ending).
  3. Compound-name conflicts (substring matches that risk double-annotation).
  4. Chip-syntax sanity (cooking commands match `<N> <Min.|Sek.>/.../<Stufe ...>`).
  5. Step structure: each step ~40-130 chars (BLOCK > 240), max one cooking chip per
     step (one operation per step). See native-style-rules.md.

Usage: ./audit-recipe.py recipe.json
Prints findings, exits 0 if clean / 1 if any BLOCKer.
"""
import sys, re, json, pathlib
from collections import Counter

# Keywords the AI annotator matches by substring → canonical form. Multiple
# spellings (plural, compound) collapse to one canonical so the uniqueness
# check counts them as the same ingredient.
INGREDIENT_NORMALIZATION = {
    # plurals
    "Auberginen": "Aubergine", "Karotten": "Karotte", "Schalotten": "Schalotte",
    "Gurken": "Gurke", "Limetten": "Limette", "Frühlingszwiebeln": "Frühlingszwiebel",
    "Chilischoten": "Chilischote", "Chili": "Chilischote",
    "Spitzpaprikas": "Spitzpaprika", "Erdnüssen": "Erdnüsse",
    # synonym-pairs that the AI treats as the same chip
    "Buschbohnen": "Bohnen", "Bohne": "Bohnen",
    "Basmatireis": "Reis",
    "Limettenspalten": "Limette", "Limettenviertel": "Limette",
    "Filetstücke": "Filetstück", "Filetstücken": "Filetstück",
    # subtle variants
    "Soße": "Sauce", "Soßen": "Sauce",
    "Hello Curry": "Hello Curry",  # identity (just to register the canonical name)
}

# Canonical forms we actually look for. Anything not in this list is ignored
# (avoids false positives like every preposition).
CANONICAL_INGREDIENTS = {
    "Wasser", "Salz", "Pfeffer", "Zucker", "Öl", "Sesamöl", "Mehl", "Reis",
    "Aubergine", "Bohnen", "Karotte", "Schalotte", "Spitzpaprika",
    "Gurke", "Frühlingszwiebel", "Chilischote",
    "Limette", "Erdnüsse",
    "Mayonnaise", "Sriracha", "Sweet-Chili", "Teriyaki", "Teriyakisoße",
    "Tomatenmark", "Ketjap", "Sojasoße", "Hello Curry", "Curry",
    "Filetstück", "Tofu", "Edamame",
}

ADJACENT_END_PATTERNS = [
    "abschmecken", "verrühren", "vermengen", "marinieren",
    "köcheln lassen", "ziehen lassen", "ruhen lassen",
    "beiseitestellen", "beiseite stellen", "servieren",
]

# Step-length quality gates (native median ≈ 90 chars, see native-style-rules.md).
STEP_LEN_SOFT_MAX = 180   # > this = too dense, WARN (split it)
STEP_LEN_HARD_MAX = 240   # > this = BLOCK (definitely crams multiple operations)


def normalize_ending(s: str) -> str:
    """Strip trailing period, collapse ALL whitespace, lowercase.

    Collapsing all whitespace makes 'beiseitestellen' == 'beiseite stellen'
    after normalization — both become 'beiseitestellen' for comparison.
    """
    s = s.rstrip().rstrip(".").lower()
    s = re.sub(r"\s+", "", s)
    return s


def detect_ingredients_in_step(step: str) -> Counter:
    """Count canonical ingredient mentions in a step.

    Algorithm:
      1. Build search-term list = CANONICAL ∪ NORMALIZATION_KEYS
      2. Sort DESC by length so longer terms match first (Buschbohnen > Bohnen,
         Chilischoten > Chilischote > Chili)
      3. For each term, find non-overlapping matches in regions not yet consumed
         by an earlier (longer) match. Map matched text to canonical form.

    This handles:
    - Plural variants: `Chilischoten` matched once (longest), not twice
      (once as 'Chilischoten' once as 'Chilischote\\w*')
    - Synonym pairs: `Buschbohnen ... Bohnen` both count as canonical 'Bohnen'
    """
    counts = Counter()
    all_terms = set(CANONICAL_INGREDIENTS) | set(INGREDIENT_NORMALIZATION.keys())
    # Longest first so overlapping shorter terms don't double-count
    sorted_terms = sorted(all_terms, key=len, reverse=True)
    consumed = [False] * len(step)
    for term in sorted_terms:
        pattern = rf"\b{re.escape(term)}\w*\b"
        for m in re.finditer(pattern, step):
            if any(consumed[m.start():m.end()]):
                continue
            canonical = INGREDIENT_NORMALIZATION.get(term, term)
            counts[canonical] += 1
            for i in range(m.start(), m.end()):
                consumed[i] = True
    return counts


def check_per_step_uniqueness(steps):
    findings = []
    for i, step in enumerate(steps, 1):
        counts = detect_ingredients_in_step(step)
        dupes = {kw: n for kw, n in counts.items() if n > 1}
        for kw, n in dupes.items():
            findings.append(("WARN", f"Step {i}: '{kw}' mentioned {n}x (across variants) — will produce duplicate chips"))
    return findings


def check_adjacent_endings(steps):
    findings = []
    for i in range(1, len(steps)):
        prev_end = normalize_ending(steps[i-1])
        curr_end = normalize_ending(steps[i])
        # Check if both end with the same known phrase
        for pat in ADJACENT_END_PATTERNS:
            if prev_end.endswith(pat) and curr_end.endswith(pat):
                findings.append(("BLOCK", f"Steps {i} and {i+1} both end with '...{pat}' — reads like copy-paste, merge or rephrase"))
                break
        else:
            # Also catch when the literal last word matches (catches variants not in the pattern list)
            prev_last = prev_end.split()[-1] if prev_end.split() else ""
            curr_last = curr_end.split()[-1] if curr_end.split() else ""
            if prev_last and prev_last == curr_last and len(prev_last) > 5:
                findings.append(("WARN", f"Steps {i} and {i+1} both end with word '{prev_last}' — consider varying the last verb"))
    return findings


def check_compound_conflicts(steps, ingredients):
    """If a step mentions e.g. 'Sriracha-Mayo' while 'Sriracha-Sauce' is an ingredient,
    the AI will annotate both → two chips for 'Sriracha'.
    """
    findings = []
    ing_keywords = set()
    for ing in ingredients:
        for w in re.findall(r"[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜa-zäöüß]+)*", ing):
            # rstrip dashes just in case
            w = w.rstrip("-")
            if len(w) > 4 and w not in {"Stück", "Prise", "Teelöffel", "Esslöffel"}:
                ing_keywords.add(w)
    for i, step in enumerate(steps, 1):
        hits = Counter()
        for kw in ing_keywords:
            # Use word boundary so trailing dashes don't matter
            n = len(re.findall(rf"\b{re.escape(kw)}\b", step))
            if n > 1:
                hits[kw] = n
        for kw, n in hits.items():
            findings.append(("WARN", f"Step {i}: token '{kw}' appears {n}x — check if compound names cause double-annotation"))
    return findings


def check_chip_syntax(steps):
    findings = []
    chips_found = 0
    for i, step in enumerate(steps, 1):
        candidates = re.findall(r"\b\d+(?:[-–]\d+)?\s+(?:Sek|Min)\.\/[^.,;]{3,80}", step)
        for c in candidates:
            chips_found += 1
            if not re.search(r"/Stufe\s+", c) and "/Varoma" not in c and "/Teig" not in c:
                findings.append(("WARN", f"Step {i}: '{c.strip()}' may not annotate as TTS chip (needs /Stufe N or /Varoma)"))
    if chips_found == 0:
        findings.append(("WARN", "No cooking-command chip patterns detected — this recipe will have no interactive Thermomix chips"))
    return findings, chips_found


def _count_chips(step: str) -> int:
    return len(re.findall(r"\b\d+(?:[-–]\d+)?\s+(?:Sek|Min)\.\/", step))


PARALLEL_MARKER_RE = re.compile(r"\b(Währenddessen|In der Zwischenzeit|In dieser Zeit|In den letzten)\b", re.I)


def check_step_structure(steps, ingredients):
    """Philosophy (refined 2026-05-29 against fresh native top-recipe dump): the gate is
    "one ACTIVE operation per step", NOT a char limit. Native recipes routinely have
    200-380-char steps when the length comes from (a) a running chip + parallel manual
    work folded in via 'In dieser Zeit …', or (b) the final assembly/serve step. So we
    no longer BLOCK on length — only WARN on an *unjustified* long step (not the final
    step, no parallel-prep marker). The real two-operations tell is >1 cooking chip in
    one step → that IS a blocker. See native-style-rules.md rule 2.
    """
    findings = []
    n_step = len(steps)
    last_idx = n_step - 1
    flagged = 0
    for i, step in enumerate(steps):
        L = len(step)
        chips = _count_chips(step)
        justified = (i == last_idx) or PARALLEL_MARKER_RE.search(step) or chips >= 1
        if L > STEP_LEN_HARD_MAX and not justified:
            findings.append(("WARN", f"Step {i+1}: {L} chars and no running chip / parallel marker / final-step — likely crams several active handgriffs. Split it."))
            flagged += 1
        elif L > STEP_LEN_SOFT_MAX and not justified:
            findings.append(("WARN", f"Step {i+1}: {L} chars — long with no chip running. Check it is one active operation, else split."))
            flagged += 1
        if chips > 1:
            findings.append(("BLOCK", f"Step {i+1}: {chips} cooking-command chips in one step = two active machine operations. Split into {chips} steps."))
            flagged += 1
    if flagged == 0:
        findings.append(("OK", f"{n_step} steps, each one active operation (length OK where a chip runs or it's the final step)"))
    return findings


def check_fused_operations(steps):
    """Catch the class of bug a length/chip count misses: a manual-prep operation
    fused with a SEPARATE machine/pan operation in one step.

    Good (NOT flagged): manual prep folded into a *running* machine step via
    'Währenddessen …' / 'In der Zwischenzeit …' — that is the intended native pattern.
    Bad (flagged): a step that first does a standalone manual prep (e.g. 'Tofu in
    Scheiben hobeln.') and THEN starts a new operation (a Mixtopf chip, or heating a
    pan + frying) without that parallel-prep marker. Those should be two steps.

    Heuristic, so WARN not BLOCK. Examples it would have caught on 2026-05-29:
      - Thai #32 step 1: '… auspressen. … in den Mixtopf geben, 5 Sek./Stufe 4 …'
      - Räuchertofu #25 step 10: '… hobeln. In einer Pfanne … erhitzen und … anbraten.'
    """
    PREP_VERB = re.compile(r"\b(auspress\w+|hobel\w+|würfel\w+|raspel\w+|press\w+)\b\.?\s*$", re.I)
    NEW_PAN = re.compile(r"in einer? (?:großen )?Pfanne[^.]*\b(erhitz\w+|anbrat\w+|brat\w+)", re.I)
    PARALLEL_MARKER = re.compile(r"^\s*(Währenddessen|In der Zwischenzeit|In dieser Zeit|In den letzten)", re.I)
    findings = []
    for i, step in enumerate(steps, 1):
        if PARALLEL_MARKER.search(step):
            continue  # deliberate parallel prep into a running machine step — fine
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", step) if s.strip()]
        if len(sentences) < 2:
            continue
        prep_sentence = any(PREP_VERB.search(s) for s in sentences[:-1])
        if not prep_sentence:
            continue
        rest = " ".join(sentences)
        has_new_op = _count_chips(step) >= 1 or NEW_PAN.search(rest)
        if has_new_op:
            findings.append(("WARN", f"Step {i}: manual prep fused with a separate machine/pan operation — split into two steps (or fold the prep into a running step via 'Währenddessen …')."))
    return findings


def main():
    if len(sys.argv) != 2:
        print("usage: audit-recipe.py recipe.json", file=sys.stderr); sys.exit(64)
    try:
        data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"error: cannot read recipe JSON: {e}", file=sys.stderr); sys.exit(64)
    if "ingredients" not in data or "steps" not in data:
        print("error: input JSON must have 'ingredients' and 'steps' keys", file=sys.stderr); sys.exit(64)

    ingredients = data["ingredients"]
    steps = data["steps"]

    print(f"Auditing recipe: {len(ingredients)} ingredients, {len(steps)} steps")
    print()

    all_findings = []
    all_findings += check_step_structure(steps, ingredients)
    all_findings += check_fused_operations(steps)
    all_findings += check_per_step_uniqueness(steps)
    all_findings += check_adjacent_endings(steps)
    all_findings += check_compound_conflicts(steps, ingredients)
    chip_findings, n_chips = check_chip_syntax(steps)
    all_findings += chip_findings

    blockers = [f for sev, f in all_findings if sev == "BLOCK"]
    warns = [f for sev, f in all_findings if sev == "WARN"]
    oks = [f for sev, f in all_findings if sev == "OK"]

    for f in oks: print(f"  ✓ {f}")
    if warns:
        print()
        for f in warns: print(f"  ⚠ {f}")
    if blockers:
        print()
        for f in blockers: print(f"  ✗ {f}")

    print()
    print(f"TTS chip candidates detected: {n_chips}")
    print(f"Blockers: {len(blockers)} · Warnings: {len(warns)}")
    sys.exit(1 if blockers else 0)


if __name__ == "__main__":
    main()
