"use client";

import { usePathname } from "next/navigation";
import { useRef, useState } from "react";

import { useApp } from "@/components/app/AppProvider";
import ThemeToggle from "@/components/app/ThemeToggle";
import { PAGE_TITLES, pageRoot } from "@/lib/routes";
import { threadDate } from "@/lib/threadGroups";
import { HOVER_CARD_DELAY_MS } from "@/lib/ui";

/** The persistent top bar. Stays mounted across pages: an own page shows its name; a chat shows its title on one
 *  line, cut with "…", and a hover card with the full title, date, messages and project (BACKLOG L24 U7). */
export default function Navbar() {
  const app = useApp();
  const pathname = usePathname();
  const [card, setCard] = useState(false);
  const timer = useRef<number | null>(null);
  const hoverEnd = () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    setCard(false);
  };
  const pageTitle = PAGE_TITLES[pageRoot(pathname)];   // a form page (/settings/users/3) shows its page's name
  const thread = app.history.threads.find((t) => t.id === app.history.activeThreadId);
  const title = pageTitle || app.chat.title;
  const subtitle = pageTitle
    ? app.auth.user?.username || ""
    : `${app.runMode} · ${app.specialists.label}${app.history.activeProject ? ` · ${app.history.activeProject.name}` : ""}`;

  return (
    <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line bg-glass px-4 py-[0.55rem]">
      <div
        className="relative min-w-0"
        onMouseEnter={() => {
          hoverEnd();
          timer.current = window.setTimeout(() => setCard(true), HOVER_CARD_DELAY_MS);
        }}
        onMouseLeave={hoverEnd}
      >
        <strong className="block truncate text-[1rem] [font-family:var(--font-display)]">{title}</strong>
        <span className="mt-[0.1rem] block truncate text-[0.72rem] text-muted">{subtitle}</span>
        {card && !pageTitle && thread ? (
          <div
            role="tooltip"
            className="pointer-events-none absolute left-0 top-full z-50 mt-1 w-max max-w-[min(28rem,80vw)] rounded-xl border border-line bg-panel px-3 py-2 shadow-shadow"
          >
            <p className="m-0 text-[0.88rem] leading-snug text-text">{thread.title}</p>
            <p className="m-0 mt-1 text-[0.72rem] text-muted">
              {threadDate(thread)} · {thread.jobIds.length} message{thread.jobIds.length === 1 ? "" : "s"}
              {app.history.activeProject ? ` · ${app.history.activeProject.name}` : ""}
            </p>
          </div>
        ) : null}
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
