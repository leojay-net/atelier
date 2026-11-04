import type { Metadata } from "next";
import { AuthForm } from "@/components/auth/AuthForm";
import { login } from "../actions";

export const metadata: Metadata = { title: "Sign in · Atelier" };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; expired?: string }>;
}) {
  const { next, expired } = await searchParams;
  return (
    <AuthForm
      mode="login"
      action={login}
      next={next?.startsWith("/") ? next : "/"}
      notice={expired ? "Your session ended. Please sign in again." : undefined}
    />
  );
}
