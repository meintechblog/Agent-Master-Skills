-- Knowledge Base — PostgreSQL + pgvector Schema (domain-agnostic template)
-- Hybrid Search: tsvector (FTS) + vector (semantic) with Reciprocal Rank Fusion.
--
-- Two enums are domain-specific and marked with TODO below: source_type and category.
-- They MUST match the ALLOWED_SOURCE_TYPES / ALLOWED_CATEGORIES sets in ingest.py.
-- The article_type and status enums are the recommended default — keep as-is unless you
-- deliberately adjust your taxonomy (see references/article-standard.md §A).

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Documents: one row per ingested source file.
-- Frontmatter-as-schema — see references/article-standard.md §A. Enums validated
-- hard in ingest.py; the DB CHECKs are a backstop.
CREATE TABLE IF NOT EXISTS documents (
  id              BIGSERIAL PRIMARY KEY,
  slug            TEXT UNIQUE NOT NULL,
  title           TEXT NOT NULL,
  summary         TEXT,
  category        TEXT NOT NULL,
  -- TODO: define your domain's controlled vocabulary for `category` and add it as a
  -- CHECK constraint here, OR leave category un-CHECKed and rely on ingest.py validation.
  -- Example for a software-tool KB:
  --   CHECK (category IN ('installation','configuration','api','cli','auth',
  --                       'integrations','troubleshooting','concepts','misc'))
  subcategory     TEXT,
  source_type     TEXT NOT NULL CHECK (source_type IN (
    -- TODO: define your domain's controlled vocabulary for `source_type`.
    -- This list MUST match ALLOWED_SOURCE_TYPES in ingest.py. Example placeholder set:
    'official-docs','community-forum','github',
    'manual-pdf','live-doc','own-findings','blog','memory'
  )),
  -- KB Article Standard fields (article_type/status = recommended default enums) --------
  article_type    TEXT CHECK (article_type IS NULL OR article_type IN
    ('how-to','reference','concept','troubleshooting','faq','site-state')),
  scope           TEXT,                          -- general | site:<your-instance>
  info_type       TEXT,                          -- Information Mapping hint (optional)
  status          TEXT CHECK (status IS NULL OR status IN
    ('draft','verified','unverified-reprint','deprecated','archived')),
  questions       TEXT[] DEFAULT '{}'::TEXT[],   -- natural-language questions the article answers
  aliases         TEXT[] DEFAULT '{}'::TEXT[],   -- verbatim lexical anchors + DE<->EN synonyms
  applies_to_device  TEXT,
  applies_to_version TEXT,
  applies_to_site    TEXT,
  verified_at     DATE,
  owner           TEXT,
  review_cadence  TEXT,
  related         TEXT[] DEFAULT '{}'::TEXT[],    -- slugs of related articles
  canonical_ref   TEXT,                           -- slug of the generic article a site-state points to
  language        TEXT,                           -- de | en | mixed
  ui_paths        TEXT[] DEFAULT '{}'::TEXT[],     -- verified click-/command-paths (exact-match boost + no-hallucination audit)
  source_url      TEXT,
  internal_path   TEXT NOT NULL,
  last_updated    TIMESTAMPTZ NOT NULL,
  ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  has_own_content BOOLEAN NOT NULL DEFAULT FALSE,
  tags            TEXT[] DEFAULT '{}'::TEXT[]
);

CREATE INDEX IF NOT EXISTS idx_documents_category     ON documents(category, subcategory);
CREATE INDEX IF NOT EXISTS idx_documents_source       ON documents(source_type);
CREATE INDEX IF NOT EXISTS idx_documents_own          ON documents(has_own_content);
CREATE INDEX IF NOT EXISTS idx_documents_updated      ON documents(last_updated DESC);
CREATE INDEX IF NOT EXISTS idx_documents_tags         ON documents USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_documents_article_type ON documents(article_type);
CREATE INDEX IF NOT EXISTS idx_documents_scope        ON documents(scope);
CREATE INDEX IF NOT EXISTS idx_documents_status       ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_questions    ON documents USING GIN(questions);

-- Chunks: per-document split units, each with its own embedding.
-- multilingual-e5-small produces 384-dim embeddings.
-- chunk_kind: body = a heading-split section; summary/question = virtual
-- multi-vector rows (answer-first summary + each natural-language question),
-- embedded so question-style queries match question-style surfaces (HyPE-lite).
CREATE TABLE IF NOT EXISTS chunks (
  id            BIGSERIAL PRIMARY KEY,
  doc_id        BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index   INTEGER NOT NULL,
  chunk_kind    TEXT NOT NULL DEFAULT 'body',   -- body | summary | question
  heading_path  TEXT,                           -- e.g. "Steps > Set X & Y"
  content       TEXT NOT NULL,
  token_count   INTEGER,
  embedding     vector(384),                    -- multilingual-e5-small (change dim if you swap models)
  -- question/summary rows are pure 'A' lexical surfaces; body rows keep heading='A', content='B'.
  tsv           tsvector GENERATED ALWAYS AS (
    CASE
      WHEN chunk_kind IN ('summary','question')
        THEN setweight(to_tsvector('simple', coalesce(content,'')), 'A')
      ELSE
        setweight(to_tsvector('simple', coalesce(heading_path,'')), 'A') ||
        setweight(to_tsvector('simple', coalesce(content,'')), 'B')
    END
  ) STORED,
  UNIQUE (doc_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc          ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_tsv          ON chunks USING GIN(tsv);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding    ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_kind         ON chunks(chunk_kind);

-- Stats view for a home/dashboard (cheap to compute, served fresh on every request).
-- The per-source-type counts below reference the placeholder source_type values — adjust
-- them to match YOUR controlled vocabulary (or drop the ones you don't use).
CREATE OR REPLACE VIEW v_stats AS
SELECT
  (SELECT COUNT(*) FROM documents)                                            AS document_count,
  (SELECT COUNT(*) FROM chunks)                                               AS chunk_count,
  (SELECT COUNT(DISTINCT source_type) FROM documents)                         AS source_type_count,
  (SELECT COUNT(*) FROM documents WHERE source_type = 'official-docs')        AS official_docs_count,
  (SELECT COUNT(*) FROM documents WHERE source_type = 'community-forum')      AS community_forum_count,
  (SELECT COUNT(*) FROM documents WHERE source_type = 'github')               AS github_count,
  (SELECT COUNT(*) FROM documents WHERE source_type = 'manual-pdf')           AS pdf_count,
  (SELECT COUNT(*) FROM documents WHERE source_type = 'live-doc')             AS live_doc_count,
  (SELECT COUNT(*) FROM documents WHERE source_type = 'own-findings')         AS own_findings_count,
  (SELECT COUNT(*) FROM documents WHERE source_type = 'blog')                 AS blog_count,
  (SELECT COUNT(*) FROM documents WHERE source_type = 'memory')               AS memory_count,
  (SELECT pg_database_size(current_database()))                               AS db_size_bytes,
  (SELECT MAX(ingested_at) FROM documents)                                    AS last_ingest_at;

-- Audit trail for re-ingest runs
CREATE TABLE IF NOT EXISTS ingest_runs (
  id              BIGSERIAL PRIMARY KEY,
  started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at     TIMESTAMPTZ,
  documents_added INTEGER DEFAULT 0,
  documents_updated INTEGER DEFAULT 0,
  documents_removed INTEGER DEFAULT 0,
  chunks_total    INTEGER DEFAULT 0,
  notes           TEXT
);

-- Notification trigger for SSE: announce when documents change (optional; for live UIs)
CREATE OR REPLACE FUNCTION notify_doc_change() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('doc_change', json_build_object(
    'op', TG_OP,
    'doc_id', COALESCE(NEW.id, OLD.id),
    'slug', COALESCE(NEW.slug, OLD.slug)
  )::text);
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS documents_notify ON documents;
CREATE TRIGGER documents_notify
  AFTER INSERT OR UPDATE OR DELETE ON documents
  FOR EACH ROW EXECUTE FUNCTION notify_doc_change();
