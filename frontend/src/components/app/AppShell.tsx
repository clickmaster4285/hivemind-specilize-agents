"use client";

import type { ReactNode } from "react";

import { useApp } from "@/components/app/AppProvider";
import Navbar from "@/components/app/Navbar";
import Sidebar from "@/components/app/Sidebar";
import AuthScreen from "@/components/chat/AuthScreen";

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
        onSignIn={app.auth.signIn}
      />
    );
  }

  return (
    <div className="app-shell-grid">
      <Sidebar />
      <div className="app-right">
        <Navbar />
        <main className="app-main">{children}</main>
      </div>
    </div>
  );
}
