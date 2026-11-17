"use client";

import { useState } from "react";

import { useApp } from "@/components/app/AppProvider";
import PageHeader, { pageSearch, pageTab } from "@/components/app/PageHeader";
import SkillsSection from "@/components/settings/skills/SkillsSection";

/** Connectors planned for later (the user's choice, 2026-11-17: a placeholder now, real connectors later). */
const PLANNED = [
  { name: "Gmail", about: "Read and draft email" },
  { name: "Google Drive", about: "Find and read your documents" },
  { name: "GitHub", about: "Issues, pull requests and code" },
  { name: "Slack", about: "Read and send messages" },
  { name: "Microsoft 365", about: "Outlook mail, calendar and files" },
  { name: "Google Search Console", about: "Your site's search performance" },
];

type Tab = "all" | "connectors" | "skills";

/** /plugins (BACKLOG L24 U5): connectors and skills in one place, like Claude's Plugins — All · Connectors ·
 *  Skills. Skills moved here from Settings (the user's ask, 2026-11-17); connectors are planned, not built. */
export default function PluginsPage() {
  const app = useApp();
  const [tab, setTab] = useState<Tab>("all");
  const [search, setSearch] = useState("");
  const user = app.auth.user;
  if (!user) return null; // the shell shows the login when signed out
  const canSkills = Boolean(user.modules?.includes("skills"));
  const needle = search.trim().toLowerCase();
  const connectors = PLANNED.filter((p) => !needle || `${p.name} ${p.about}`.toLowerCase().includes(needle));
  const tabs: { id: Tab; label: string }[] = [
    { id: "all", label: "All" },
    { id: "connectors", label: "Connectors" },
    ...(canSkills ? [{ id: "skills" as const, label: "Skills" }] : []),
  ];

  return (
    <PageHeader
      title="Plugins"
      about="What MulteAgent can connect to and the skills it can use."
      toolbar={
        <>
          <input
            className={pageSearch}
            placeholder={tab === "skills" ? "Search in the skills list below" : "Search connectors"}
            value={search}
            disabled={tab === "skills"}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div role="tablist" aria-label="Show" className="flex gap-1 rounded-xl border border-line p-1">
            {tabs.map((t) => (
              <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} className={pageTab(tab === t.id)}
                      onClick={() => setTab(t.id)}>
                {t.label}
              </button>
            ))}
          </div>
        </>
      }
    >
      {tab !== "skills" ? (
        <section className="mt-7">
          <h2 className="m-0 text-[1rem] font-semibold">Connectors</h2>
          <p className="mt-1 text-[0.82rem] text-muted">Not available yet — these are planned.</p>
          <ul className="m-0 mt-3 grid list-none grid-cols-2 gap-x-6 p-0 max-[700px]:grid-cols-1">
            {connectors.map((p) => (
              <li key={p.name} className="flex items-center justify-between gap-3 border-b border-line py-3">
                <span className="min-w-0">
                  <span className="block text-[0.92rem] text-text">{p.name}</span>
                  <span className="block truncate text-[0.78rem] text-muted">{p.about}</span>
                </span>
                <span className="shrink-0 rounded-full border border-line px-2 py-0.5 text-[0.68rem] text-muted">Planned</span>
              </li>
            ))}
            {connectors.length === 0 ? <li className="py-3 text-[0.82rem] text-muted">No connector matches.</li> : null}
          </ul>
        </section>
      ) : null}

      {tab !== "connectors" && canSkills ? (
        <section className="mt-8 flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
          <SkillsSection skills={app.skills} specialists={app.specialists} />
        </section>
      ) : null}
    </PageHeader>
  );
}
