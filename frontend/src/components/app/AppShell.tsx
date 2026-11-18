"use client";

import { useState, type ReactNode } from "react";

import { useApp } from "@/components/app/AppProvider";
import ChatUrlSync from "@/components/app/ChatUrlSync";
import Navbar from "@/components/app/Navbar";
import Sidebar from "@/components/app/Sidebar";
import AuthScreen from "@/components/auth/AuthScreen";

/** Whether the sidebar is collapsed, remembered per browser (a per-viewer convenience, BACKLOG L24 U2). */
const COLLAPSED_KEY = "multeagent_sidebar_collapsed";

function readCollapsed(): boolean {
  try {
    return typeof window !== "undefined" && window.localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;                       // storage blocked: the sidebar simply starts open
  }
}

function saveCollapsed(collapsed: boolean): void {
  try {
    window.localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch {
    // storage blocked: the choice lasts for this page only
  }
}

/** The persistent shell: sidebar + navbar stay mounted; only `children` (the page) changes.
 *  Before sign-in, the whole screen is the login. */
export default function AppShell({ children }: { children: ReactNode }) {
  const app = useApp();
  // read once on mount: the shell only renders after sign-in, in the browser, so there is no server markup to match
  const [collapsed, setCollapsed] = useState(readCollapsed);

  if (!app.auth.user) {
    return (
      <AuthScreen
        error={app.auth.error}
        captchaRequired={app.auth.captchaRequired}
        onSignIn={app.auth.signIn}
      />
    );
  }

  function toggleCollapsed() {
    setCollapsed((v) => {
      saveCollapsed(!v);
      return !v;
    });
  }

  return (
    <div
      className={`grid h-full max-h-dvh max-w-[100vw] overflow-hidden transition-[grid-template-columns] duration-200 max-[900px]:grid-cols-1 max-[900px]:grid-rows-[minmax(12rem,38vh)_1fr] ${
        collapsed ? "grid-cols-[60px_1fr]" : "grid-cols-[272px_1fr]"
      }`}
    >
      <ChatUrlSync />
      <Sidebar collapsed={collapsed} onToggleCollapsed={toggleCollapsed} />
      <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-[color-mix(in_srgb,var(--ink)_35%,transparent)] max-[900px]:h-auto">
        <Navbar />
        <main className="flex min-h-0 flex-1 flex-col overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}
