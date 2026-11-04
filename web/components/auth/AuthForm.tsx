"use client";

import Link from "next/link";
import { useActionState } from "react";
import type { AuthState } from "@/app/(auth)/actions";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";

type Action = (state: AuthState, form: FormData) => Promise<AuthState>;

const input =
  "w-full rounded-sm border border-border-strong bg-bg px-3 py-2 text-sm text-fg outline-none " +
  "focus:border-accent";

export function AuthForm({
  mode,
  action,
  next,
  notice,
}: {
  mode: "login" | "signup";
  action: Action;
  next: string;
  notice?: string;
}) {
  const [state, formAction, pending] = useActionState(action, undefined);
  const signup = mode === "signup";

  return (
    <Card className="w-full max-w-sm p-7">
      <h1 className="text-lg font-semibold tracking-tight text-fg">
        {signup ? "Create your account" : "Sign in to Atelier"}
      </h1>
      <p className="mt-1 text-[13px] text-fg-secondary">
        {signup
          ? "Your uploads and edits are private to your account."
          : "Welcome back. Your workspace is waiting."}
      </p>

      {notice && !state?.error && (
        <p className="mt-4 rounded-sm bg-bg-subtle px-3 py-2 text-[13px] text-fg-secondary">
          {notice}
        </p>
      )}
      {state?.error && (
        <p
          role="alert"
          className="mt-4 rounded-sm border border-status-failed/40 bg-status-failed/10 px-3 py-2 text-[13px] text-status-failed"
        >
          {state.error}
        </p>
      )}

      <form action={formAction} className="mt-5 flex flex-col gap-4">
        <input type="hidden" name="next" value={next} />
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium text-fg">Email</span>
          <input
            name="email"
            type="email"
            autoComplete="email"
            required
            defaultValue={state?.email}
            className={input}
          />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium text-fg">Password</span>
          <input
            name="password"
            type="password"
            autoComplete={signup ? "new-password" : "current-password"}
            required
            minLength={signup ? 8 : undefined}
            className={input}
          />
          {signup && (
            <span className="text-[12px] text-fg-secondary">
              At least 8 characters, not only numbers.
            </span>
          )}
        </label>
        {signup && (
          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] font-medium text-fg">Confirm password</span>
            <input
              name="password_confirm"
              type="password"
              autoComplete="new-password"
              required
              minLength={8}
              className={input}
            />
          </label>
        )}
        <Button type="submit" loading={pending} className="mt-1 w-full">
          {signup ? "Create account" : "Sign in"}
        </Button>
      </form>

      <p className="mt-5 text-center text-[13px] text-fg-secondary">
        {signup ? "Already have an account? " : "New to Atelier? "}
        <Link
          href={`${signup ? "/login" : "/signup"}${next !== "/" ? `?next=${encodeURIComponent(next)}` : ""}`}
          className="font-medium text-accent hover:underline"
        >
          {signup ? "Sign in" : "Create an account"}
        </Link>
      </p>
    </Card>
  );
}
