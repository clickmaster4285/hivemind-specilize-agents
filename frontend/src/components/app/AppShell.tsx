"use client";

import type { ReactNode } from "react";

import { useApp } from "@/components/app/AppProvider";
import Navbar from "@/components/app/Navbar";
import Sidebar from "@/components/app/Sidebar";
import AuthScreen from "@/components/auth/AuthScreen";

/** The persistent shell: sidebar + navbar stay mounted; only `children` (the page) changes.
 *  Before sign-in, the whole screen is the login. */
export default function AppShell({ children }: { children: ReactNode }) {
  const app = useApp();

  if (!app.auth.user) {
    return (
      <AuthScreen
        health={app.health.health}
        healthError={app.health.error}
        error={app.auth.error}
        captchaRequired={app.auth.captchaRequired}
        onSignIn={app.auth.signIn}
      />
    );
  }

  return (
    <div className="grid h-full max-h-dvh max-w-[100vw] grid-cols-[280px_1fr] overflow-hidden max-[900px]:grid-cols-1 max-[900px]:grid-rows-[minmax(12rem,38vh)_1fr]">
      <Sidebar />
      <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-[color-mix(in_srgb,var(--ink)_35%,transparent)] max-[900px]:h-auto">
        <Navbar />
        <main className="flex min-h-0 flex-1 flex-col overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}
