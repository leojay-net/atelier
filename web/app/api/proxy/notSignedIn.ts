import "server-only";

/** What the proxy answers with no session cookie: the same JSON error shape as Django. */
export function notSignedIn(): Response {
  return Response.json(
    { error: "not_signed_in", message: "Sign in to continue." },
    { status: 401 },
  );
}
