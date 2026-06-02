const EMBED_URL = process.env.EMBED_URL || "http://127.0.0.1:8770";

/** Embed a search query via the local e5 embedding service (query: prefix added there). */
export async function embedQuery(text: string): Promise<number[] | null> {
  try {
    const res = await fetch(`${EMBED_URL}/embed`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texts: [text], kind: "query" }),
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as { embeddings: number[][] };
    return data.embeddings?.[0] ?? null;
  } catch {
    return null;
  }
}

export function toVectorLiteral(v: number[]): string {
  return "[" + v.map((x) => Number(x).toFixed(7)).join(",") + "]";
}
