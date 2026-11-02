"use client";

import { usePathname } from "next/navigation";

import { useApp } from "@/components/app/AppProvider";
import ThemeToggle from "@/components/app/ThemeToggle";

/** The persistent top bar. Stays mounted across pages; shows the current page's title and status. */
export default function Navbar() {
  const app = useApp();
  const pathname = usePathname();
  const onSettings = pathname?.startsWith("/settings");
  const title = onSettings ? "Settings" : app.chat.title;
  const subtitle = onSettings
    ? app.auth.user?.username || ""
    : `${app.runMode} · ${app.specialists.label}${app.history.activeProject ? ` · ${app.history.activeProject.name}` : ""}`;

  return (
    <header className="app-navbar">
      <div className="app-navbar-title">
        <strong>{title}</strong>
        <span>{subtitle}</span>
      </div>
      <div className="app-navbar-right">
        <div className="chat-toolbar">
          <select
            value={app.specialists.choice}
            onChange={(e) => app.specialists.setChoice(e.target.value)}
            title="Who answers: Auto lets the assistant pick a specialist for each message"
          >
            <option value="auto">Specialist: auto</option>
            {app.specialists.specialists
              .filter((s) => s.enabled)
              .map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            <option value="none">No specialist</option>
          </select>
        </div>
        <ThemeToggle />
      </div>
    </header>
  );
}
