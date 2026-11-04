import type { Metadata } from "next";
import { AuthForm } from "@/components/auth/AuthForm";
import { signup } from "../actions";

export const metadata: Metadata = { title: "Create account · Atelier" };

export default async function SignupPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  const { next } = await searchParams;
  return <AuthForm mode="signup" action={signup} next={next?.startsWith("/") ? next : "/"} />;
}
