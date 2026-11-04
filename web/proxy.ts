import { NextResponse, type NextRequest } from "next/server";

/**
 * Optimistic sign-in check (Next 16 "Proxy", formerly Middleware): a page request without a
 * session cookie goes to /login, and a signed-in visitor opening /login or /signup goes home.
 * It only looks at the cookie's presence; Django validates the key on every API call, and an
 * invalid one lands on /logout, which clears it. API routes answer 401 themselves.
 */
const SESSION_COOKIE = "atelier_session"; // lib/server/session.ts (server-only, so not imported)
const AUTH_PAGES = ["/login", "/signup"];

function to(req: NextRequest, location: string): NextResponse {
  // Absolute (Proxy requires it), on the host the browser used: behind the nginx reverse proxy
  // that is the Host header plus X-Forwarded-Proto, not the container's own address.
  const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host") ?? req.nextUrl.host;
  const forwarded = req.headers.get("x-forwarded-proto")?.split(",")[0]?.trim();
  const proto = forwarded || req.nextUrl.protocol.replace(":", "");
  return NextResponse.redirect(new URL(location, `${proto}://${host}`), 307);
}

export function proxy(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  const signedIn = Boolean(req.cookies.get(SESSION_COOKIE)?.value);
  const authPage = AUTH_PAGES.includes(pathname);
  if (!signedIn && !authPage) {
    const next = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname + search)}`;
    return to(req, `/login${next}`);
  }
  if (signedIn && authPage) return to(req, "/");
  return NextResponse.next();
}

export const config = {
  // Everything except API routes, /logout, Next's own files and static assets.
  matcher: ["/((?!api/|logout|_next/static|_next/image|favicon.ico|.*\\.(?:png|svg|jpg|ico|webp)$).*)"],
};
