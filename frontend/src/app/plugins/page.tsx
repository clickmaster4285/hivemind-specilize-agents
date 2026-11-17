"use client";

import { useApp } from "@/components/app/AppProvider";

/** Connectors planned for later (the user's choice, 2026-11-17: a placeholder page now, real connectors later). */
const PLANNED = [
  { name: "Gmail", about: "Read and draft email" },
  { name: "Google Drive", about: "Find and read your documents" },
  { name: "GitHub", about: "Issues, pull requests and code" },
  { name: "Slack", about: "Read and send messages" },
  { name: "Microsoft 365", about: "Outlook mail, calendar and files" },
  { name: "Google Search Console", about: "Your site's search performance" },
];

/** /plugins (BACKLOG L24 U5): where connections to the user's other tools will live. A placeholder for now. */
export default function PluginsPage() {
  const app = useApp();
  if (!app.auth.user) return null; // the shell shows the login when signed out
  return (
    <div className="mx-auto w-full max-w-[880px] px-4 py-8">
      <h1 className="m-0 text-[1.6rem] [font-family:var(--font-display)]">Plugins</h1>
      <p className="mt-1 text-[0.9rem] text-muted">Connect MulteAgent to the tools you already use.</p>

      <div className="mt-6 rounded-xl border border-dashed border-line bg-panel-2 px-4 py-3 text-[0.85rem] text-muted">
        Connectors are not available yet. These are planned:
      </div>

      <ul className="m-0 mt-4 grid list-none grid-cols-2 gap-x-6 p-0 max-[700px]:grid-cols-1">
        {PLANNED.map((p) => (
          <li key={p.name} className="flex items-center justify-between gap-3 border-b border-line py-3">
            <span className="min-w-0">
              <span className="block text-[0.92rem] text-text">{p.name}</span>
              <span className="block truncate text-[0.78rem] text-muted">{p.about}</span>
            </span>
            <span className="shrink-0 rounded-full border border-line px-2 py-0.5 text-[0.68rem] text-muted">Planned</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
