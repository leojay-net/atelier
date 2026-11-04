import type { NextRequest } from "next/server";
import { SESSION_COOKIE, USER_COOKIE } from "@/lib/server/session";

/**
 * Where a page lands when Django rejects the session key (revoked or expired): clear the stale
 * cookies, then go to /login. A GET, so a Server Component's redirect() can reach it; it only
 * ever removes this browser's own cookies. Signing out on purpose is the logout Server Action.
 * The Location is relative so it stays on the public host behind the nginx reverse proxy.
 */
export function GET(req: NextRequest) {
  const expired = req.nextUrl.searchParams.get("expired") ? "?expired=1" : "";
  const headers = new Headers({ Location: `/login${expired}`, "Cache-Control": "no-store" });
  for (const name of [SESSION_COOKIE, USER_COOKIE]) {
    headers.append("Set-Cookie", `${name}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax`);
  }
  return new Response(null, { status: 303, headers });
}
