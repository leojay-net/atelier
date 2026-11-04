"use server";

import { redirect } from "next/navigation";
import { apiBase } from "@/lib/server/env";
import { djangoPublicPost } from "@/lib/server/djangoFetch";
import { endSession, sessionToken, startSession } from "@/lib/server/session";

export type AuthState = { error?: string; email?: string } | undefined;

interface SessionBody {
  token: string;
  expires_in: number;
  user: { email: string };
}

/** Only same-site paths, so a crafted ?next= can't send someone to another site after login. */
function safeNext(value: FormDataEntryValue | null): string {
  const next = typeof value === "string" ? value : "";
  return next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\") ? next : "/";
}

async function finish(res: Response, email: string, next: string): Promise<AuthState> {
  let body: (SessionBody & { message?: string }) | undefined;
  try {
    body = await res.json();
  } catch {
    body = undefined;
  }
  if (!res.ok || !body?.token) {
    return {
      error: body?.message ?? "Something went wrong. Please try again.",
      email,
    };
  }
  await startSession(body.token, body.user.email, body.expires_in);
  redirect(next);
}

export async function signup(_: AuthState, form: FormData): Promise<AuthState> {
  const email = String(form.get("email") ?? "").trim();
  const password = String(form.get("password") ?? "");
  const confirm = String(form.get("password_confirm") ?? "");
  if (password !== confirm) return { error: "The passwords do not match.", email };
  const res = await djangoPublicPost("/auth/signup", {
    email,
    password,
    password_confirm: confirm,
  });
  return finish(res, email, safeNext(form.get("next")));
}

export async function login(_: AuthState, form: FormData): Promise<AuthState> {
  const email = String(form.get("email") ?? "").trim();
  const password = String(form.get("password") ?? "");
  if (!email || !password) return { error: "Enter your email and password.", email };
  const res = await djangoPublicPost("/auth/login", { email, password });
  return finish(res, email, safeNext(form.get("next")));
}

export async function logout(): Promise<void> {
  const token = await sessionToken();
  if (token) {
    // Revoke the key server-side too, so a copied cookie stops working. Best effort.
    await fetch(`${apiBase()}/auth/logout`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    }).catch(() => undefined);
  }
  await endSession();
  redirect("/login");
}
