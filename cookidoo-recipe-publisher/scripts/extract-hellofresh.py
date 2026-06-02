#!/usr/bin/env python3
"""Extract HelloFresh recipe data from a URL.

Strategy:
  1. Enumerate every `<script type="application/ld+json">…</script>` block,
     parse each, recurse into nested `@graph`/lists, collect all Recipe-typed
     objects. Pick the one whose `url`/`mainEntityOfPage` matches the input
     URL — fall back to the largest object if no preference matches.
  2. If no script-tag block contains a Recipe (HelloFresh sometimes inlines
     it in the page body via Next.js hydration), fall back to a tolerant
     brace-walker that uses the same string/escape state machine going
     BACKWARD as forward (so it doesn't pick up `{` inside string literals).

Usage: ./extract-hellofresh.py <hellofresh-url>
Prints JSON to stdout with:
  { "name", "url", "description", "servings", "prepTime_iso",
    "cookTime_iso", "totalTime_iso", "image_url", "category", "cuisine",
    "keywords", "ingredients", "instructions", "nutrition" }

Exit codes:
  0 = success
  1 = network error
  2 = parse error (no Recipe found)
  64 = usage error
"""
import sys, re, json, html as html_mod, gzip
import urllib.request, urllib.error


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (cookidoo-recipe-publisher skill)",
        # Refuse compression so the regex doesn't see gzip mojibake.
        "Accept-Encoding": "identity",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            # Defensive: server may ignore Accept-Encoding and gzip anyway.
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        print(f"network error: {e}", file=sys.stderr); sys.exit(1)


# --- Recipe discovery ----------------------------------------------------

def _collect_recipes(node, out):
    """Recurse into list/dict/@graph structures, collect Recipe-typed objects."""
    if isinstance(node, list):
        for n in node: _collect_recipes(n, out)
        return
    if not isinstance(node, dict):
        return
    t = node.get("@type")
    if t == "Recipe" or (isinstance(t, list) and "Recipe" in t):
        out.append(node)
    if "@graph" in node:
        _collect_recipes(node["@graph"], out)
    # Some pages nest Recipe under mainEntity/itemReviewed/etc.
    for k in ("mainEntity", "itemReviewed"):
        if k in node:
            _collect_recipes(node[k], out)


def find_recipes_from_script_tags(html: str) -> list:
    out = []
    for m in re.finditer(r'<script[^>]+type=[\'"]application/ld\+json[\'"][^>]*>(.+?)</script>', html, re.DOTALL):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        _collect_recipes(obj, out)
    return out


def find_recipe_from_body(html: str):
    """Tolerant brace-walker fallback — used when no script-tag holds a Recipe.
    Whitespace-tolerant @type match; backward AND forward walking respect
    string literals + escape sequences so we don't pick up `{` inside values.
    """
    m = re.search(r'"@type"\s*:\s*"Recipe"', html)
    if not m:
        return None

    # Walk backward from match start, respecting strings/escapes, to find the
    # opening brace where depth returns to 0.
    depth = 0; in_str = False
    start = None
    i = m.start() - 1
    while i >= 0:
        ch = html[i]
        # Detect string boundaries while walking backward. A `"` is a string
        # delimiter unless preceded by an odd number of `\` chars.
        if ch == '"':
            bs = 0; j = i - 1
            while j >= 0 and html[j] == "\\": bs += 1; j -= 1
            if bs % 2 == 0:
                in_str = not in_str
            i -= 1; continue
        if in_str:
            i -= 1; continue
        if ch == "}": depth += 1
        elif ch == "{":
            if depth == 0:
                start = i; break
            depth -= 1
        i -= 1
    if start is None:
        return None

    # Walk forward balancing braces (same state machine, simpler direction).
    depth = 0; in_str = False; esc = False
    for i, ch in enumerate(html[start:], start):
        if esc: esc = False; continue
        if ch == "\\": esc = True; continue
        if ch == '"' and not esc: in_str = not in_str; continue
        if in_str: continue
        if ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:i+1])
                except json.JSONDecodeError:
                    return None
    return None


def select_best_recipe(recipes: list, url: str, html: str) -> dict:
    """Prefer a Recipe whose url/mainEntityOfPage matches the input URL.
    Fall back to the largest object (likeliest to be the main recipe).
    """
    if not recipes:
        print("no Recipe found in page", file=sys.stderr); sys.exit(2)
    norm_url = url.rstrip("/")
    for r in recipes:
        for k in ("url", "mainEntityOfPage", "@id"):
            v = r.get(k)
            if isinstance(v, dict): v = v.get("@id") or v.get("url")
            if isinstance(v, str) and v.rstrip("/") == norm_url:
                return r
    # Fall back to the largest (by JSON string length)
    return max(recipes, key=lambda r: len(json.dumps(r)))


# --- Field-level extraction ---------------------------------------------

def strip_html(s: str) -> str:
    if not s: return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return html_mod.unescape(s)


def flatten_instructions(node, out):
    """Schema.org allows recipeInstructions as flat list of HowToStep OR
    a list of HowToSection objects (each with itemListElement). Handle both.
    """
    if isinstance(node, list):
        for n in node: flatten_instructions(n, out)
        return
    if isinstance(node, dict):
        t = node.get("@type")
        if t == "HowToSection" and "itemListElement" in node:
            flatten_instructions(node["itemListElement"], out); return
        text = node.get("text") or node.get("name")
        if text: out.append(strip_html(text))
        return
    if isinstance(node, str):
        out.append(strip_html(node))


def extract_image(r: dict):
    img = r.get("image")
    if isinstance(img, list) and img: img = img[0]
    if isinstance(img, dict): img = img.get("url")
    if isinstance(img, list) and img: img = img[0]
    return img if isinstance(img, str) else None


def extract_nutrition(r: dict) -> dict:
    nutr = r.get("nutrition") or {}
    if isinstance(nutr, list):
        nutr = nutr[0] if nutr else {}
    if not isinstance(nutr, dict):
        return {}
    return {k: v for k, v in nutr.items() if k != "@type" and v is not None}


def extract(url: str) -> dict:
    html = fetch(url)
    recipes = find_recipes_from_script_tags(html)
    if not recipes:
        body_obj = find_recipe_from_body(html)
        if body_obj: recipes = [body_obj]
    r = select_best_recipe(recipes, url, html)

    instructions = []
    flatten_instructions(r.get("recipeInstructions", []), instructions)

    return {
        "name": r.get("name"),
        "url": url,
        "description": r.get("description"),
        "servings": r.get("recipeYield"),
        "prepTime_iso": r.get("prepTime"),
        "cookTime_iso": r.get("cookTime"),
        "totalTime_iso": r.get("totalTime"),
        "image_url": extract_image(r),
        "category": r.get("recipeCategory"),
        "cuisine": r.get("recipeCuisine"),
        "keywords": r.get("keywords"),
        "ingredients": r.get("recipeIngredient", []),
        "instructions": instructions,
        "nutrition": extract_nutrition(r),
    }


def main():
    if len(sys.argv) != 2:
        print("usage: extract-hellofresh.py <hellofresh-url>", file=sys.stderr); sys.exit(64)
    print(json.dumps(extract(sys.argv[1]), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
