import "server-only";
import { cookies } from "next/headers";

/**
 * The signed-in user's session: their own Django API key, issued at sign-in
 * (POST /auth/login or /auth/signup) and kept in an httpOnly cookie, so browser JavaScript never
 * sees it. Every request to Django goes out with this key, which scopes it to the user's own
 * workspace; with no cookie there is no key, and nothing is sent.
 */
export const SESSION_COOKIE = "atelier_session";
export const USER_COOKIE = "atelier_user"; // the email, only for display in the sidebar

const cookieOptions = (maxAge: number) => ({
  httpOnly: true,
  // HTTPS in production; `next dev` on http://localhost still works.
  secure: process.env.NODE_ENV === "production",
  sameSite: "lax" as const,
  path: "/",
  maxAge,
});

export async function sessionToken(): Promise<string | undefined> {
  return (await cookies()).get(SESSION_COOKIE)?.value || undefined;
}

export async function sessionEmail(): Promise<string | undefined> {
  return (await cookies()).get(USER_COOKIE)?.value || undefined;
}

/** Only from a Server Function or Route Handler (cookies can't be set while rendering). */
export async function startSession(token: string, email: string, maxAge: number): Promise<void> {
  const store = await cookies();
  store.set(SESSION_COOKIE, token, cookieOptions(maxAge));
  store.set(USER_COOKIE, email, cookieOptions(maxAge));
}

/** Only from a Server Function or Route Handler. */
export async function endSession(): Promise<void> {
  const store = await cookies();
  store.delete(SESSION_COOKIE);
  store.delete(USER_COOKIE);
}
