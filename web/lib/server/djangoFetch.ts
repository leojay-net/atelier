import "server-only";
import { redirect } from "next/navigation";
import { apiBase } from "./env";
import { sessionToken } from "./session";

/**
 * The one place a request to Django is built with the real Authorization header: the signed-in
 * user's own key, from their session cookie. Guarded by the "server-only" import above: a
 * "use client" file importing this (directly, or indirectly through lib/api.ts) fails the build,
 * so "the key never reaches the browser" is enforced by the bundler, not just by review.
 *
 * For Server Components: with no session it sends the user to /login, and when Django rejects
 * the key (revoked or expired) to /logout, which clears the stale cookie first.
 */
export async function djangoFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const token = await sessionToken();
  if (!token) redirect("/login");
  const url = `${apiBase()}${path.startsWith("/") ? "" : "/"}${path}`;
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(url, { ...init, headers, cache: "no-store" });
  if (res.status === 401) redirect("/logout?expired=1");
  return res;
}

/** A request to Django's public sign-up / sign-in endpoints (no key yet). */
export async function djangoPublicPost(path: string, body: unknown): Promise<Response> {
  return fetch(`${apiBase()}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
}
