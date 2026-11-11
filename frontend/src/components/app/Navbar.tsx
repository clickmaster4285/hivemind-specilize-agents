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
    <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line bg-glass px-4 py-[0.55rem]">
      <div className="min-w-0">
        <strong className="block truncate text-[1rem] [font-family:var(--font-display)]">{title}</strong>
        <span className="mt-[0.1rem] block truncate text-[0.72rem] text-muted">{subtitle}</span>
      </div>
      <div className="flex shrink-0 items-center gap-[0.6rem]">
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 px-4 py-2">
          <select
            className="rounded-[0.55rem] border border-line bg-panel-2 px-[0.6rem] py-[0.45rem] text-text"
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
