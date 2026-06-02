"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";

interface Hit {
  slug: string;
  title: string;
  source_type: string;
  status: string | null;
  snippet: string;
}

export default function SearchBox() {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) { setHits([]); setLoading(false); return; }
    setLoading(true);
    const t = setTimeout(async () => {
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      try {
        const res = await fetch(`/api/search?q=${encodeURIComponent(term)}&limit=12`, { signal: ac.signal });
        const data = await res.json();
        setHits(data.hits ?? []);
      } catch { /* aborted */ } finally { setLoading(false); }
    }, 200);
    return () => clearTimeout(t);
  }, [q]);

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 12, padding: "14px 16px" }}>
        <span style={{ color: "var(--faint)" }}>⌕</span>
        <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Stell eine Frage …"
          style={{ flex: 1, background: "transparent", border: "none", outline: "none", color: "var(--text)", fontSize: 16 }} />
        {loading && <span style={{ color: "var(--faint)", fontSize: 12 }}>sucht …</span>}
      </div>
      {hits.length > 0 && (
        <ul style={{ listStyle: "none", padding: 0, margin: "18px 0 0", display: "flex", flexDirection: "column", gap: 10 }}>
          {hits.map((h) => (
            <li key={h.slug}>
              <Link href={`/doc/${h.slug}`} style={{ display: "block", background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 12, padding: "14px 16px" }}>
                <div style={{ fontWeight: 600, fontSize: 15.5, marginBottom: 6 }}>{h.title}</div>
                <p style={{ margin: 0, color: "#c2c2cb", fontSize: 14, lineHeight: 1.55 }} dangerouslySetInnerHTML={{ __html: h.snippet }} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
