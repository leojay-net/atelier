import "server-only";
import type { NextRequest } from "next/server";
import { apiBase, apiKey } from "@/lib/server/env";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/**
 * Dedicated SSE passthrough for GET /jobs/{id}/events (note: no ".json" suffix, unlike the
 * polling fallback at events.json, which the generic proxy route handles instead).
 *
 * Kept separate from the generic catch-all because a long-lived, never-buffered stream needs
 * headers a generic relay would not reliably get right by accident (Cache-Control, no transfer
 * encoding tricks, no compression), and because this path shape is easy to confuse with the
 * polling one.
 */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const after = req.nextUrl.searchParams.get("after");
  const lastEventId = req.headers.get("last-event-id");

  const headers = new Headers({ Authorization: `Bearer ${apiKey()}` });
  if (lastEventId) headers.set("Last-Event-ID", lastEventId);

  const upstream = await fetch(
    `${apiBase()}/jobs/${encodeURIComponent(id)}/events${after ? `?after=${encodeURIComponent(after)}` : ""}`,
    { headers, cache: "no-store" },
  );

  if (!upstream.ok || !upstream.body) {
    return new Response(upstream.body, { status: upstream.status });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
