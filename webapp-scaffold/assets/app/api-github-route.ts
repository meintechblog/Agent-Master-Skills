import { NextResponse } from "next/server";
import { fetchOpenItems } from "@/lib/github";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// GET /api/github → { repo, issues[], prs[], fetched_at, error }
export async function GET() {
  const data = await fetchOpenItems();
  return NextResponse.json(data, { status: data.error ? 200 : 200 });
}
