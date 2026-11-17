"use client";

import { useState } from "react";

import SpecialistModal from "@/components/settings/SpecialistModal";
import UsageSection from "@/components/settings/UsageSection";
import UsersSection from "@/components/settings/users/UsersSection";
import { useUsers } from "@/hooks/useUsers";
import type { SpecialistsApi } from "@/hooks/useSpecialists";
import type { AuthUser, Specialist } from "@/lib/types";
import { btn } from "@/lib/ui";

// Shared Tailwind class strings (kept here so the styling stays with the component, not in global CSS).
const panel = "rounded-xl border border-line bg-panel p-5 flex flex-col gap-3";
const heading = "text-base font-semibold [font-family:var(--font-display)]";
const hint = "text-xs leading-relaxed text-muted";
const rowCard = "flex items-center justify-between gap-3 rounded-lg border border-line bg-panel-2 px-3 py-2.5";

type TabId = "users" | "usage" | "agents";

/** The Settings content, organised into tabs: Users (admins), Usage, Agents. Skills live on the Plugins page and
 *  devices on /devices (BACKLOG L24 U5). One section shows at a time; the shell provides the navbar + sidebar. */
export default function SettingsBody({ user, specialists }: { user: AuthUser; specialists: SpecialistsApi }) {
  const isAdmin = user.role === "admin";
  // the modules an admin gave this user (admins have all): each one is a tab
  const has = (module: string) => Boolean(user.modules?.includes(module));
  const users = useUsers(isAdmin);
  // undefined = modal closed, null = new specialist
  const [editing, setEditing] = useState<Specialist | null | undefined>(undefined);

  // Admins get Users; Usage follows the user's modules; everyone gets Agents. Skills moved to the Plugins page,
  // Devices to /devices, and Models was dropped (one model, picked per message) — the user's asks, 2026-11-17.
  // The first visible tab is the default.
  const tabs: { id: TabId; label: string }[] = [
    ...(isAdmin ? [{ id: "users" as const, label: "Users" }] : []),
    ...(has("usage") ? [{ id: "usage" as const, label: "Usage" }] : []),
    { id: "agents", label: "Agents" },
  ];
  const [tab, setTab] = useState<TabId>(tabs[0].id);


  return (
    <div className="mx-auto w-full max-w-4xl p-6">
      {/* Tab bar */}
      <div className="mb-6 flex gap-1 overflow-x-auto border-b border-line">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            aria-current={tab === t.id ? "page" : undefined}
            className={`-mb-px shrink-0 border-b-2 px-4 py-2 text-sm transition-colors ${
              tab === t.id
                ? "border-copper text-text"
                : "border-transparent text-muted hover:text-text"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Users (admin) */}
      {tab === "users" && isAdmin ? (
        <section className={panel}>
          <UsersSection users={users} />
        </section>
      ) : null}

      {/* Usage (admins: everyone; members: their own) */}
      {tab === "usage" && has("usage") ? (
        <section className={panel}>
          <UsageSection />
        </section>
      ) : null}

      {/* Agents (specialists) */}
      {tab === "agents" ? (
        <section className={panel}>
          <div className="flex items-center justify-between gap-3">
            <h4 className={heading}>Agents</h4>
            {isAdmin ? (
              <button type="button" className={btn("ghost", "sm")} onClick={() => setEditing(null)}>
                New agent
              </button>
            ) : null}
          </div>
          <p className={hint}>
            With &ldquo;Specialist: auto&rdquo; the assistant reads each agent&rsquo;s &ldquo;covers&rdquo; line and
            picks one per message (or none for small talk and general questions).
          </p>
          <ul className="flex flex-col gap-2">
            {specialists.specialists.map((s) => (
              <li key={s.id} className={rowCard}>
                <span className="flex min-w-0 items-center gap-2.5">
                  <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: s.accent }} />
                  <span className="min-w-0">
                    <strong className="block truncate text-sm">
                      {s.name}
                      {s.enabled ? "" : " (off)"}
                    </strong>
                    <small className="block truncate text-xs text-muted">{s.covers}</small>
                  </span>
                </span>
                <button type="button" className={btn("ghost", "sm")} onClick={() => setEditing(s)}>
                  {isAdmin ? "Edit" : "View"}
                </button>
              </li>
            ))}
          </ul>
          {isAdmin ? (
            <button
              type="button"
              className={btn("ghost", "full")}
              onClick={() => {
                if (confirm("Restore the default IT, Development and Marketing agents?")) void specialists.reset();
              }}
            >
              Restore defaults
            </button>
          ) : null}
        </section>
      ) : null}

      {editing !== undefined ? (
        <SpecialistModal
          key={editing?.id ?? "new"}
          specialist={editing}
          options={specialists.options}
          onSave={specialists.save}
          onDelete={specialists.remove}
          onClose={() => setEditing(undefined)}
        />
      ) : null}
    </div>
  );
}
