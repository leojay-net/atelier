import "server-only";
import type { NextRequest } from "next/server";
import { apiBase } from "@/lib/server/env";
import { sessionToken } from "@/lib/server/session";
import { notSignedIn } from "../notSignedIn";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/**
 * A generic passthrough to the Django API for every call except the SSE stream (its own route,
 * app/api/proxy/stream/[id]/route.ts, which needs different headers and non-buffering behavior).
 *
 * The body is read whole with arrayBuffer() rather than relayed as a live stream: Next's route
 * handler request body (in this version) did not reliably reach an upstream fetch when passed
 * through as a raw ReadableStream (the multipart boundary arrived empty on the Django side even
 * though the headers were correct). Uploads are already capped server-side (200MB default), so
 * buffering once here is an acceptable trade for a passthrough that actually works.
 */
async function forward(req: NextRequest, segments: string[]): Promise<Response> {
  const token = await sessionToken();
  if (!token) return notSignedIn();
  const target = `${apiBase()}/${segments.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;

  const headers = new Headers();
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  headers.set("Authorization", `Bearer ${token}`);

  const hasBody = !["GET", "HEAD"].includes(req.method);
  const body = hasBody ? await req.arrayBuffer() : undefined;

  const upstream = await fetch(target, {
    method: req.method,
    headers,
    body,
    cache: "no-store",
  });

  const outHeaders = new Headers(upstream.headers);
  outHeaders.delete("content-encoding");
  outHeaders.delete("content-length");
  return new Response(upstream.body, { status: upstream.status, headers: outHeaders });
}

type RouteContext = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, { params }: RouteContext) {
  return forward(req, (await params).path);
}

export async function POST(req: NextRequest, { params }: RouteContext) {
  return forward(req, (await params).path);
}

export async function PUT(req: NextRequest, { params }: RouteContext) {
  return forward(req, (await params).path);
}

export async function DELETE(req: NextRequest, { params }: RouteContext) {
  return forward(req, (await params).path);
}

export async function PATCH(req: NextRequest, { params }: RouteContext) {
  return forward(req, (await params).path);
}
