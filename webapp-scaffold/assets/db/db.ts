import { Pool } from "pg";
import { embedQuery, toVectorLiteral } from "./embed";

/**
 * Generic hybrid-search layer for a knowledge-base web-app (webapp-scaffold).
 * Pairs with the `knowledge-base` skill's schema.sql + ingest.py + embedding-service.py.
 *
 * Hybrid: pgvector (HNSW cosine) ∥ tsvector (FTS), fused with Reciprocal Rank Fusion
 * (k=60). Multi-vector chunks (body/summary/question) are scored per doc, deduped to the
 * best body chunk for display, then biased by a trust boost (verified/official content).
 *
 * Domain note: this works against the base knowledge-base schema. If your domain adds
 * columns (e.g. valid_from/valid_to/legal_basis), add them to DOC_FIELDS + rowToMeta and,
 * if useful, extend the trust-boost CASE in hybridSearch (adapt to your domain for an
 * example that boosts currently-valid regulation).
 */

const globalForPool = globalThis as unknown as { _pgPool?: Pool };

function getPool(): Pool {
  if (!globalForPool._pgPool) {
    globalForPool._pgPool = new Pool({
      connectionString: process.env.DATABASE_URL,
      max: process.env.NODE_ENV === "production" ? 8 : 3,
    });
  }
  return globalForPool._pgPool;
}

const RRF_K = 60;
const CANDIDATES = 80;

const DOC_FIELDS = `
  d.slug, d.title, d.summary, d.category, d.subcategory, d.source_type,
  d.article_type, d.status, d.questions, d.source_url,
  to_char(d.last_updated,'YYYY-MM-DD') AS last_updated, d.related`;

export interface SearchHit {
  slug: string;
  title: string;
  summary: string | null;
  category: string;
  source_type: string;
  article_type: string | null;
  status: string | null;
  heading_path: string | null;
  snippet: string;
  score: number;
}

export async function hybridSearch(query: string, limit = 12): Promise<SearchHit[]> {
  const q = query.trim();
  if (!q) return [];
  const pool = getPool();
  const vec = await embedQuery(q);

  const sql = `
    WITH
    semantic AS (
      ${vec ? `
      SELECT c.id AS chunk_id, c.doc_id,
             row_number() OVER (ORDER BY c.embedding <=> $1::vector) AS rnk
      FROM chunks c WHERE c.embedding IS NOT NULL
      ORDER BY c.embedding <=> $1::vector LIMIT ${CANDIDATES}
      ` : `SELECT NULL::bigint AS chunk_id, NULL::bigint AS doc_id, NULL::bigint AS rnk WHERE false`}
    ),
    fts AS (
      SELECT c.id AS chunk_id, c.doc_id,
             row_number() OVER (ORDER BY ts_rank(c.tsv, plainto_tsquery('simple', $2)) DESC) AS rnk
      FROM chunks c WHERE c.tsv @@ plainto_tsquery('simple', $2)
      ORDER BY ts_rank(c.tsv, plainto_tsquery('simple', $2)) DESC LIMIT ${CANDIDATES}
    ),
    fused AS (
      SELECT COALESCE(s.chunk_id, f.chunk_id) AS chunk_id,
             COALESCE(s.doc_id, f.doc_id) AS doc_id,
             COALESCE(1.0/(${RRF_K} + s.rnk), 0) + COALESCE(1.0/(${RRF_K} + f.rnk), 0) AS score
      FROM semantic s FULL OUTER JOIN fts f ON s.chunk_id = f.chunk_id
    ),
    doc_scores AS (SELECT doc_id, SUM(score) AS doc_score FROM fused GROUP BY doc_id),
    best_chunk AS (
      SELECT DISTINCT ON (f.doc_id) f.doc_id, c.heading_path, c.content
      FROM fused f JOIN chunks c ON c.id = f.chunk_id
      ORDER BY f.doc_id, (c.chunk_kind = 'body') DESC, f.score DESC
    )
    SELECT ${DOC_FIELDS}, bc.heading_path, bc.content AS chunk_content,
           (ds.doc_score
             * CASE d.status WHEN 'verified' THEN 1.15 WHEN 'unverified-reprint' THEN 0.9
                             WHEN 'deprecated' THEN 0.5 WHEN 'archived' THEN 0.5 ELSE 1.0 END
           ) AS final_score
    FROM doc_scores ds
    JOIN documents d ON d.id = ds.doc_id
    LEFT JOIN best_chunk bc ON bc.doc_id = ds.doc_id
    ORDER BY final_score DESC LIMIT $3`;

  const params = vec ? [toVectorLiteral(vec), q, limit] : [null, q, limit];
  const { rows } = await pool.query(sql, params);
  return rows.map((r) => ({
    slug: r.slug as string,
    title: r.title as string,
    summary: (r.summary as string) ?? null,
    category: r.category as string,
    source_type: r.source_type as string,
    article_type: (r.article_type as string) ?? null,
    status: (r.status as string) ?? null,
    heading_path: (r.heading_path as string) ?? null,
    snippet: buildSnippet((r.chunk_content as string) ?? r.summary ?? "", q),
    score: Number(r.final_score),
  }));
}

export async function getDoc(slug: string) {
  const pool = getPool();
  const { rows } = await pool.query(
    `SELECT ${DOC_FIELDS},
            (SELECT string_agg(content, E'\n\n' ORDER BY chunk_index)
             FROM chunks WHERE doc_id = d.id AND chunk_kind = 'body') AS body
     FROM documents d WHERE d.slug = $1`,
    [slug],
  );
  return rows[0] ?? null;
}

export async function getStats() {
  const pool = getPool();
  const { rows } = await pool.query(`SELECT * FROM v_stats`);
  return rows[0] ?? null;
}

export interface DocListItem {
  slug: string;
  title: string;
  summary: string | null;
  category: string;
  article_type: string | null;
  status: string | null;
  last_updated: string;
}

/**
 * Flat list of all documents for browse-by-category navigation. Generic — groups by
 * whatever `category` values exist; the UI prettifies labels and applies an optional
 * display order (see categories.ts). No domain-specific columns required.
 */
export async function listDocs(): Promise<DocListItem[]> {
  const pool = getPool();
  const { rows } = await pool.query(
    `SELECT d.slug, d.title, d.summary, d.category, d.article_type, d.status,
            to_char(d.last_updated,'YYYY-MM-DD') AS last_updated
     FROM documents d
     ORDER BY d.category, d.title`,
  );
  return rows.map((r) => ({
    slug: r.slug as string,
    title: r.title as string,
    summary: (r.summary as string) ?? null,
    category: r.category as string,
    article_type: (r.article_type as string) ?? null,
    status: (r.status as string) ?? null,
    last_updated: r.last_updated as string,
  }));
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);
}

function buildSnippet(content: string, query: string, window = 280): string {
  const clean = content.replace(/^Kurzantwort:\s*/i, "").replace(/\s+/g, " ").trim();
  const terms = query.toLowerCase().split(/\s+/).filter((t) => t.length >= 3);
  let start = 0;
  for (const t of terms) {
    const idx = clean.toLowerCase().indexOf(t);
    if (idx >= 0) { start = Math.max(0, idx - 60); break; }
  }
  let slice = clean.slice(start, start + window);
  if (start > 0) slice = "… " + slice;
  if (start + window < clean.length) slice = slice + " …";
  let html = escapeHtml(slice);
  for (const t of terms) {
    html = html.replace(new RegExp(`(${t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"), "<mark>$1</mark>");
  }
  return html;
}
