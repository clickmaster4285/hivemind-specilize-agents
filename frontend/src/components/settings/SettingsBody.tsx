"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { WIDE_PAGE_WIDTH } from "@/components/app/PageHeader";
import AccountSection from "@/components/settings/AccountSection";
import UsageSection from "@/components/settings/UsageSection";
import UsersSection from "@/components/settings/users/UsersSection";
import { useUsers } from "@/hooks/useUsers";
import type { SpecialistsApi } from "@/hooks/useSpecialists";
import { agentPath, settingsTabPath, type SettingsTab } from "@/lib/routes";
import type { AuthUser } from "@/lib/types";
import { btn } from "@/lib/ui";

// Shared Tailwind class strings (kept here so the styling stays with the component, not in global CSS).
const panel = "rounded-xl border border-line bg-panel p-5 flex flex-col gap-3";
const heading = "text-base font-semibold [font-family:var(--font-display)]";
const hint = "text-xs leading-relaxed text-muted";
const rowCard = "flex items-center justify-between gap-3 rounded-lg border border-line bg-panel-2 px-3 py-2.5";

/** The Settings content, organised into tabs: Users (admins), Usage, Agents. Skills live on the Plugins page and
 *  devices on /devices (BACKLOG L24 U5). One section shows at a time; the shell provides the navbar + sidebar.
 *  The tab is in the address (/settings?tab=agents), so coming back from a user's or an agent's own page
 *  (BACKLOG L19b D5) lands on the tab it was opened from. */
export default function SettingsBody({ user, specialists }: { user: AuthUser; specialists: SpecialistsApi }) {
  const isAdmin = user.role === "admin";
  // the modules an admin gave this user (admins have all): each one is a tab
  const has = (module: string) => Boolean(user.modules?.includes(module));
  const users = useUsers(isAdmin);
  const router = useRouter();
  const asked = useSearchParams().get("tab");

  // Admins get Users; Usage follows the user's modules; everyone gets Agents. Skills moved to the Plugins page,
  // Devices to /devices, and Models was dropped (one model, picked per message) — the user's asks, 2026-11-17.
  // The first visible tab is the default.
  const tabs: { id: SettingsTab; label: string }[] = [
    ...(isAdmin ? [{ id: "users" as const, label: "Users" }] : []),
    ...(has("usage") ? [{ id: "usage" as const, label: "Usage" }] : []),
    { id: "agents", label: "Agents" },
    { id: "account", label: "My account" },
  ];
  const tab = tabs.find((t) => t.id === asked)?.id ?? tabs[0].id;
  const setTab = (id: SettingsTab) => router.replace(settingsTabPath(id), { scroll: false });

  return (
    <div className={`mx-auto w-full ${WIDE_PAGE_WIDTH} px-4 py-6`}>
      {/* Tab bar */}
      {/* scrolls sideways only if the tabs do not fit; no scrollbar drawn (the tabs' -1px overlap made one) */}
      <div className="mb-6 flex gap-1 overflow-x-auto overflow-y-hidden border-b border-line [scrollbar-width:none]">
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

      {/* My account (everyone, BACKLOG L30 T2) */}
      {tab === "account" ? (
        <section className={panel}>
          <AccountSection />
        </section>
      ) : null}

      {/* Agents (specialists) */}
      {tab === "agents" ? (
        <section className={panel}>
          <div className="flex items-center justify-between gap-3">
            <h4 className={heading}>Agents</h4>
            {isAdmin ? (
              <Link href={agentPath("new")} className={btn("ghost", "sm")}>
                New agent
              </Link>
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
                <Link href={agentPath(s.id)} className={`${btn("ghost", "sm")} shrink-0`}>
                  {isAdmin ? "Edit" : "View"}
                </Link>
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
    </div>
  );
}
