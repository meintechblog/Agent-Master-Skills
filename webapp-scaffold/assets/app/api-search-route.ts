import { NextRequest } from "next/server";
import { hybridSearch } from "@/lib/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const q = sp.get("q") ?? "";
  const limit = Math.min(Number(sp.get("limit") ?? 12) || 12, 30);
  try {
    const hits = await hybridSearch(q, limit);
    return Response.json({ q, engine: "hybrid-pgvector", count: hits.length, hits });
  } catch (err) {
    const message = err instanceof Error ? err.message : "search_failed";
    return Response.json({ error: "search_failed", message, q, hits: [] }, { status: 500 });
  }
}
