import "server-only";
import { apiBase, apiKey } from "./env";

/**
 * The one place a request to Django is built with the real Authorization header. Guarded by
 * the "server-only" import above: a "use client" file importing this (directly, or indirectly
 * through lib/api.ts) fails the build, so "the key never reaches the browser" is enforced by
 * the bundler, not just by review discipline.
 */
export async function djangoFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const base = apiBase();
  const url = `${base}${path.startsWith("/") ? "" : "/"}${path}`;
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${apiKey()}`);
  return fetch(url, { ...init, headers, cache: "no-store" });
}
