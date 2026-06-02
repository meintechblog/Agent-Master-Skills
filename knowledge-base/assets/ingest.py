"""
Knowledge Base Content-Ingest-Pipeline (KB Article Standard, multi-vector retrieval).
Domain-agnostic template — see references/methodology.md + references/article-standard.md.

Reads Markdown / PDF documents from CONTENT_ROOT, parses the KB Article Standard
frontmatter, chunks each doc heading-aware, and upserts into Postgres
(documents + chunks).

Retrieval features implemented here:
  - e5 prefix contract: body/summary embedded as `passage:`, question rows as
    `query:` (the embedding service adds the literal prefix per `kind`).
  - Deterministic Contextual Retrieval: each body chunk is embedded WITH a
    `[<title> · … · Abschnitt: …]` context prefix; the RAW body is stored/returned.
  - Multi-vector per article: one `summary` row (passage) + one row per
    `questions[]` entry (query), so question-style queries match question-style
    surfaces (HyPE-lite, no generated answers → no hallucinated paths).
  - Heading-aware chunking with a ~1800-char secondary cap so context+body stays
    under e5's 512-token ceiling.

Frontmatter overrides directory inference. Closed enums are validated → an
unknown value raises (the file is skipped and reported), per the standard.

Idempotent: re-ingest replaces all chunks of a doc but preserves the doc id.

*** DOMAIN ADAPTATION (the only required edits) ***
  1. ALLOWED_SOURCE_TYPES  — must match the source_type CHECK list in schema.sql.
  2. ALLOWED_CATEGORIES    — must match your category vocabulary in schema.sql.
  3. infer_source()        — optional: map your content/ directory layout to defaults
                             (only a fallback; frontmatter always wins).
"""

import argparse
import os
import re
import sys
from datetime import datetime, timezone, date
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

try:
    import yaml  # PyYAML — robust frontmatter (block lists + nested applies_to)
except ImportError:  # pragma: no cover
    yaml = None

PG_DSN = os.environ.get(
    "DATABASE_URL", "postgres://kb:<password>@127.0.0.1:5432/kb"
)
EMBED_URL = os.environ.get("EMBED_URL", "http://127.0.0.1:8765")
CONTENT_ROOT = Path(os.environ.get("CONTENT_ROOT", "./content"))

# ----- Closed enums (validated → fail loud) ----------------------------------
# TODO (DOMAIN): define your own controlled vocabulary. These MUST match the
# source_type / category CHECK lists in schema.sql. Placeholder example sets below.
ALLOWED_SOURCE_TYPES = {
    # *** define your own controlled vocabulary for your domain ***
    "official-docs", "community-forum", "github", "manual-pdf",
    "live-doc", "own-findings", "blog", "memory",
}
ALLOWED_CATEGORIES = {
    # *** define your own controlled vocabulary for your domain ***
    "installation", "configuration", "api", "cli", "auth",
    "integrations", "troubleshooting", "concepts", "misc",
}
# article_type / status = recommended default enums (keep as-is unless you adjust taxonomy)
ALLOWED_ARTICLE_TYPES = {
    "how-to", "reference", "concept", "troubleshooting", "faq", "site-state",
}
ALLOWED_STATUS = {
    "draft", "verified", "unverified-reprint", "deprecated", "archived",
}

BODY_MAX_CHARS = 1800   # ~400-450 tokens, leaves room for the context prefix under e5's 512
BODY_OVERLAP = 240


# ----- Source-type inference (fallback when frontmatter is absent) ------------

def infer_source(rel_path: Path) -> tuple[str, str, str | None]:
    """Returns (category, source_type, subcategory) from the directory layout.
    TODO (DOMAIN): adjust this mapping to your content/ folder names. It is only a
    fallback — frontmatter `category`/`source_type` always override it. Every value
    used here MUST be in ALLOWED_CATEGORIES / ALLOWED_SOURCE_TYPES above."""
    parts = rel_path.parts
    name = parts[0] if parts else ""
    if name == "knowledge" and len(parts) > 1:
        return infer_source(Path(*parts[1:]))
    mapping = {
        # Diátaxis-typed folders → generic defaults
        "how-to":          ("misc", "own-findings", None),
        "reference":       ("misc", "own-findings", None),
        "concept":         ("concepts", "own-findings", None),
        "troubleshooting": ("troubleshooting", "own-findings", None),
        "faq":             ("misc", "own-findings", None),
        "site-state":      ("misc", "own-findings", None),
        "own-findings":    ("misc", "own-findings", None),
        "memory":          ("misc", "memory", None),
        "reports":         ("misc", "own-findings", "report"),
        # Source-typed folders
        "official-docs":   ("misc", "official-docs", None),
        "live-docs":       ("api", "live-doc", None),
        "community-forum": ("misc", "community-forum", None),
        "github":          ("api", "github", None),
        "blog":            ("misc", "blog", None),
    }
    if name in mapping:
        return mapping[name]
    return ("misc", "own-findings", None)


# ----- Frontmatter parser -----------------------------------------------------

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _mini_yaml(block: str) -> dict:
    """Tiny fallback parser: top-level scalars, `- ` block lists, and one level
    of nested mapping (applies_to:). Only used if PyYAML is unavailable."""
    out: dict = {}
    cur_list = None
    nested_key = None
    for raw in block.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()
        if line.startswith("- "):
            if cur_list is not None:
                cur_list.append(line[2:].strip().strip('"').strip("'"))
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip()
            v = v.strip()
            if indent >= 2 and nested_key:
                out.setdefault(nested_key, {})[k] = v.strip('"').strip("'")
                continue
            cur_list = None
            nested_key = None
            if v == "":
                cur_list = []
                out[k] = cur_list
                nested_key = k
            elif v.startswith("["):
                out[k] = [t.strip().strip('"').strip("'")
                          for t in v.strip("[]").split(",") if t.strip()]
            else:
                out[k] = v.strip('"').strip("'")
    return out


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return ({}, text)
    block = m.group(1)
    body = text[m.end():]
    if yaml is not None:
        try:
            fm = yaml.safe_load(block) or {}
            if not isinstance(fm, dict):
                fm = {}
        except Exception:
            fm = _mini_yaml(block)
    else:
        fm = _mini_yaml(block)
    return (fm, body)


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    if not s:
        return []
    if s.startswith("["):
        return [t.strip().strip('"').strip("'") for t in s.strip("[]").split(",") if t.strip()]
    return [s]


# ----- Chunking ---------------------------------------------------------------

def split_by_headings(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines()
    chunks: list[tuple[str, str]] = []
    h1 = h2 = h3 = ""
    buffer: list[str] = []

    def flush():
        body = "\n".join(buffer).strip()
        if body:
            path = " > ".join(filter(None, [h1, h2, h3]))
            chunks.append((path or h1 or "Top", body))

    for line in lines:
        if line.startswith("# ") and not line.startswith("##"):
            flush(); buffer = []; h1 = line[2:].strip(); h2 = h3 = ""
        elif line.startswith("## "):
            flush(); buffer = []; h2 = line[3:].strip(); h3 = ""
        elif line.startswith("### "):
            flush(); buffer = []; h3 = line[4:].strip()
        else:
            buffer.append(line)
    flush()
    return chunks


def further_chunk(text: str, max_chars: int = BODY_MAX_CHARS, overlap: int = BODY_OVERLAP) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    paras = text.split("\n\n")
    out: list[str] = []
    cur = ""
    for p in paras:
        if len(cur) + len(p) + 2 < max_chars:
            cur = (cur + "\n\n" + p) if cur else p
        else:
            if cur:
                out.append(cur)
            cur = p
    if cur:
        out.append(cur)
    overlapped: list[str] = []
    prev = ""
    for c in out:
        overlapped.append((prev[-overlap:] + "\n\n" + c) if (prev and overlap > 0) else c)
        prev = c
    return overlapped


# ----- Embeddings -------------------------------------------------------------

def embed_batch(texts: list[str], kind: str = "passage") -> list[list[float]]:
    if not texts:
        return []
    out: list[list[float]] = []
    with httpx.Client(timeout=120) as client:
        for i in range(0, len(texts), 32):
            r = client.post(f"{EMBED_URL}/embed", json={"texts": texts[i:i+32], "kind": kind})
            r.raise_for_status()
            out.extend(r.json()["embeddings"])
    return out


def vec_literal(v: list[float]) -> str:
    return "[" + ",".join(repr(round(float(x), 7)) for x in v) + "]"


# ----- DB helpers -------------------------------------------------------------

DOC_COLUMNS = [
    "slug", "title", "summary", "category", "subcategory", "source_type",
    "article_type", "scope", "info_type", "status", "questions", "aliases",
    "applies_to_device", "applies_to_version", "applies_to_site", "verified_at",
    "owner", "review_cadence", "related", "canonical_ref", "language", "ui_paths",
    "source_url", "internal_path", "last_updated", "has_own_content", "tags",
]


def upsert_document(cur, meta: dict) -> int:
    cols = ", ".join(DOC_COLUMNS)
    placeholders = ", ".join(["%s"] * len(DOC_COLUMNS))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in DOC_COLUMNS if c != "slug")
    cur.execute(
        f"INSERT INTO documents ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT (slug) DO UPDATE SET {updates} RETURNING id",
        [meta[c] for c in DOC_COLUMNS],
    )
    return cur.fetchone()["id"]


def replace_chunks(cur, doc_id: int, rows: list[tuple[str, str, str, list[float]]]):
    """rows: (chunk_kind, heading_path, content, embedding)."""
    cur.execute("DELETE FROM chunks WHERE doc_id = %s", (doc_id,))
    for i, (kind, heading, content, vec) in enumerate(rows):
        cur.execute(
            "INSERT INTO chunks (doc_id, chunk_index, chunk_kind, heading_path, content, token_count, embedding) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s::vector)",
            (doc_id, i, kind, heading, content, len(content) // 4, vec_literal(vec)),
        )


# ----- Title/summary fallbacks ------------------------------------------------

def slug_for(rel_path: Path) -> str:
    s = re.sub(r"[^a-zA-Z0-9_\-/]+", "-", str(rel_path.with_suffix("")))
    return s.replace("/", "--").strip("-").lower()


def title_from_markdown(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
        if line.strip():
            return line.strip()[:120]
    return fallback


def summary_from_markdown(text: str) -> str:
    paras = [p.strip() for p in text.split("\n\n")
             if p.strip() and not p.startswith("#") and not p.startswith(">")]
    if not paras:
        return ""
    first = re.sub(r"^Kurzantwort:\s*", "", paras[0])
    return (first[:300] + "…") if len(first) > 300 else first


def extract_pdf_text(fp: Path) -> str:
    try:
        import pypdf
    except ImportError:
        return ""
    out = []
    with fp.open("rb") as f:
        reader = pypdf.PdfReader(f)
        for i, page in enumerate(reader.pages, 1):
            try:
                txt = page.extract_text() or ""
            except Exception:
                txt = ""
            if txt.strip():
                out.append(f"## Page {i}\n\n{txt.strip()}")
    return "\n\n".join(out)


# ----- Validation -------------------------------------------------------------

def _validate(meta: dict, rel: Path):
    def bad(field, val, allowed):
        raise ValueError(f"VALIDATION FAIL {rel}: {field}='{val}' not in {sorted(allowed)}")
    if meta["source_type"] not in ALLOWED_SOURCE_TYPES:
        bad("source_type", meta["source_type"], ALLOWED_SOURCE_TYPES)
    if meta["category"] not in ALLOWED_CATEGORIES:
        bad("category", meta["category"], ALLOWED_CATEGORIES)
    if meta.get("article_type") and meta["article_type"] not in ALLOWED_ARTICLE_TYPES:
        bad("article_type", meta["article_type"], ALLOWED_ARTICLE_TYPES)
    if meta.get("status") and meta["status"] not in ALLOWED_STATUS:
        bad("status", meta["status"], ALLOWED_STATUS)


# ----- Context prefix ---------------------------------------------------------

def context_prefix(meta: dict, heading_path: str) -> str:
    """Deterministic Contextual-Retrieval prefix prepended before EMBEDDING only
    (the raw body is what gets stored/returned)."""
    bits = [meta.get("title") or "", meta.get("category") or ""]
    dev = meta.get("applies_to_device")
    ver = meta.get("applies_to_version")
    if dev and dev != "all":
        bits.append(dev)
    if ver and ver != "all":
        bits.append(ver)
    head = " · ".join(b for b in bits if b)
    sect = f" · Abschnitt: {heading_path}" if heading_path and heading_path != "Top" else ""
    return f"[{head}{sect}] "


# ----- Main ingest ------------------------------------------------------------

def ingest_file(cur, fp: Path, run_stats: dict):
    rel = fp.relative_to(CONTENT_ROOT)

    if fp.suffix.lower() == ".pdf":
        category, _, subcategory = infer_source(rel)
        source_type = "manual-pdf"
        body = extract_pdf_text(fp)
        if not body.strip():
            print(f"  WARN {fp}: empty PDF text", flush=True)
            return
        fm = {}
        title = fp.stem.replace("_", " ").replace("-", " ")
        summary = body.split("\n\n", 1)[0][:300]
    else:
        category, source_type, subcategory = infer_source(rel)
        text = fp.read_text(encoding="utf-8", errors="ignore")
        fm, body = parse_frontmatter(text)
        title = fm.get("title") or fm.get("name") or title_from_markdown(body, fp.stem)
        summary = fm.get("summary") or fm.get("description") or summary_from_markdown(body)
        if fm.get("category"):
            category = str(fm["category"]).strip()
        if str(fm.get("source_type", "")).strip():
            source_type = str(fm["source_type"]).strip()
        if fm.get("subcategory"):
            subcategory = str(fm["subcategory"]).strip()

    applies = fm.get("applies_to") or {}
    if not isinstance(applies, dict):
        applies = {}

    source_url = fm.get("source_url") or None
    if not source_url:
        for line in body.splitlines()[:15]:
            mm = re.search(r"(?:Source|Quelle)\s*[:=]\s*(https?://\S+)", line, re.IGNORECASE)
            if mm:
                source_url = mm.group(1).rstrip(")")
                break

    def _date(v):
        if isinstance(v, date):
            return v
        if not v:
            return None
        try:
            return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

    mtime = datetime.fromtimestamp(fp.stat().st_mtime, tz=timezone.utc)
    questions = _as_list(fm.get("questions"))
    meta = {
        "slug": fm.get("slug") or slug_for(rel),
        "title": title,
        "summary": summary,
        "category": category,
        "subcategory": subcategory,
        "source_type": source_type,
        "article_type": (str(fm["article_type"]).strip() if fm.get("article_type") else None),
        "scope": (str(fm["scope"]).strip() if fm.get("scope") else None),
        "info_type": (str(fm["info_type"]).strip() if fm.get("info_type") else None),
        "status": (str(fm["status"]).strip() if fm.get("status") else None),
        "questions": questions,
        "aliases": _as_list(fm.get("aliases")),
        "applies_to_device": (str(applies.get("device")).strip() if applies.get("device") else None),
        "applies_to_version": (str(applies.get("version")).strip() if applies.get("version") else None),
        "applies_to_site": (str(applies.get("site")).strip() if applies.get("site") else None),
        "verified_at": _date(fm.get("verified_at")),
        "owner": (str(fm["owner"]).strip() if fm.get("owner") else None),
        "review_cadence": (str(fm["review_cadence"]).strip() if fm.get("review_cadence") else None),
        "related": _as_list(fm.get("related")),
        "canonical_ref": (str(fm["canonical_ref"]).strip() if fm.get("canonical_ref") else None),
        "language": (str(fm["language"]).strip() if fm.get("language") else None),
        "ui_paths": _as_list(fm.get("ui_paths")),
        "source_url": source_url,
        "internal_path": str(rel),
        "last_updated": _date(fm.get("last_updated")) or mtime,
        "has_own_content": source_type in ("own-findings", "memory"),
        "tags": _as_list(fm.get("tags")),
    }
    _validate(meta, rel)

    doc_id = upsert_document(cur, meta)

    # ---- Body chunks (heading-aware, secondary cap) ----
    sections = split_by_headings(body) or [("Top", body)]
    body_units: list[tuple[str, str]] = []
    for heading, section in sections:
        for sub in further_chunk(section):
            body_units.append((heading, sub))
    if not body_units:
        body_units = [("Top", body[:BODY_MAX_CHARS])]

    body_embed_texts = [context_prefix(meta, h) + c for (h, c) in body_units]
    body_vecs = embed_batch(body_embed_texts, kind="passage")
    rows: list[tuple[str, str, str, list[float]]] = [
        ("body", h, c, v) for (h, c), v in zip(body_units, body_vecs)
    ]

    # ---- Multi-vector: summary row (passage) ----
    if meta["summary"]:
        sv = embed_batch([context_prefix(meta, "") + meta["summary"]], kind="passage")
        if sv:
            rows.append(("summary", meta["title"], meta["summary"], sv[0]))

    # ---- Multi-vector: one row per question (query) ----
    if questions:
        qvecs = embed_batch(questions, kind="query")
        for q, qv in zip(questions, qvecs):
            rows.append(("question", meta["title"], q, qv))

    replace_chunks(cur, doc_id, rows)
    run_stats["chunks_total"] += len(rows)
    run_stats["documents_added"] += 1


def main():
    # ingest_file() rechnet fp.relative_to(CONTENT_ROOT) gegen das Modul-Global —
    # daher hier deklarieren, damit der per --content übergebene (oft ABSOLUTE) Root
    # unten in CONTENT_ROOT übernommen wird (sonst ValueError "not in subpath" → 0 docs).
    global CONTENT_ROOT
    p = argparse.ArgumentParser()
    p.add_argument("--content", default=str(CONTENT_ROOT))
    p.add_argument("--prune", action="store_true",
                   help="delete documents whose source file no longer exists")
    args = p.parse_args()
    root = Path(args.content)
    if not root.exists():
        print(f"content root {root} not found", file=sys.stderr)
        sys.exit(1)
    CONTENT_ROOT = root

    run_stats = {"chunks_total": 0, "documents_added": 0, "documents_updated": 0, "documents_removed": 0}
    if yaml is None:
        print("  NOTE: PyYAML not installed — using fallback frontmatter parser.", flush=True)

    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO ingest_runs (started_at) VALUES (NOW()) RETURNING id")
            run_id = cur.fetchone()["id"]
            conn.commit()

            seen_slugs: set[str] = set()
            count = 0
            fails = 0
            files = sorted(list(root.rglob("*.md")) + list(root.rglob("*.pdf")))
            for fp in files:
                if any(part.startswith(".") for part in fp.relative_to(root).parts):
                    continue
                try:
                    rel = fp.relative_to(root)
                    text = fp.read_text(encoding="utf-8", errors="ignore") if fp.suffix == ".md" else ""
                    fm, _ = parse_frontmatter(text) if text else ({}, "")
                    seen_slugs.add(fm.get("slug") or slug_for(rel))
                    ingest_file(cur, fp, run_stats)
                    count += 1
                    if count % 10 == 0:
                        conn.commit()
                        print(f"  ... {count} docs ingested", flush=True)
                except Exception as e:
                    fails += 1
                    print(f"  WARN {fp}: {e}", flush=True)
            conn.commit()

            if args.prune and seen_slugs:
                cur.execute("DELETE FROM documents WHERE slug <> ALL(%s) RETURNING slug",
                            (list(seen_slugs),))
                removed = cur.fetchall()
                run_stats["documents_removed"] = len(removed)
                for r in removed:
                    print(f"  pruned {r['slug']}", flush=True)
                conn.commit()

            cur.execute(
                "UPDATE ingest_runs SET finished_at=NOW(), documents_added=%s, "
                "documents_removed=%s, chunks_total=%s, notes=%s WHERE id=%s",
                (run_stats["documents_added"], run_stats["documents_removed"],
                 run_stats["chunks_total"], f"{fails} file(s) failed validation", run_id),
            )
            conn.commit()

    print(f"Done. {count} documents, {run_stats['chunks_total']} chunks, "
          f"{run_stats['documents_removed']} pruned, {fails} failed.")


if __name__ == "__main__":
    main()
