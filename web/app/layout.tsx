import type { Metadata } from "next";
import { AppShell } from "@/components/layout/AppShell";
import { ToastProvider } from "@/components/errors/ToastProvider";
import "./globals.css";

export const metadata: Metadata = {
  title: "Atelier",
  description: "A deterministic media engine with an AI director on top.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="antialiased">
        <ToastProvider>
          <AppShell>{children}</AppShell>
        </ToastProvider>
      </body>
    </html>
  );
}
