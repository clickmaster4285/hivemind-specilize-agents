"use client";

import { useState } from "react";

import ServersSection from "@/components/chat/ServersSection";
import SpecialistModal from "@/components/chat/SpecialistModal";
import { useServers } from "@/hooks/useServers";
import type { ModelsApi } from "@/hooks/useModels";
import type { SpecialistsApi } from "@/hooks/useSpecialists";
import type { AuthUser, Specialist } from "@/lib/types";

// Shared Tailwind class strings (kept here so the styling stays with the component, not in global CSS).
const panel = "rounded-xl border border-[var(--line)] bg-[var(--panel)] p-5 flex flex-col gap-3";
const heading = "text-base font-semibold [font-family:var(--font-display)]";
const hint = "text-xs leading-relaxed text-[var(--muted)]";
const rowCard = "flex items-center justify-between gap-3 rounded-lg border border-[var(--line)] bg-[var(--panel-2)] px-3 py-2.5";

/** The Settings content (servers, models, agents). Styling is inline Tailwind; the shell provides the
 *  navbar + sidebar. Servers and Agents have a create panel + list; Models is a read-only list. */
export default function SettingsBody({
  user,
  specialists,
  models,
}: {
  user: AuthUser;
  specialists: SpecialistsApi;
  models: ModelsApi;
}) {
  const isAdmin = user.role === "admin";
  const servers = useServers(isAdmin);
  // undefined = modal closed, null = new specialist
  const [editing, setEditing] = useState<Specialist | null | undefined>(undefined);

  const liveModels = models.models.filter((m) => m.enabled && m.available !== false);

  return (
    <div className="mx-auto w-full max-w-6xl p-6">
      <div className="grid items-start gap-6 lg:grid-cols-2">
        {/* ---- One side: Servers (create + list) ---- */}
        {isAdmin ? (
          <section className={panel}>
            <ServersSection servers={servers} />
          </section>
        ) : null}

        {/* ---- Other side: Models, then Agents ---- */}
        <div className="flex flex-col gap-6">
          {/* Models */}
          <section className={panel}>
            <div className="flex items-center justify-between gap-3">
              <h4 className={heading}>Models</h4>
              <button type="button" className="btn ghost sm" onClick={() => void models.refresh(true)}>
                Sync
              </button>
            </div>
            <ul className="flex flex-col gap-2">
              {liveModels.map((m) => (
                <li key={m.name} className={rowCard}>
                  <div className="min-w-0">
                    <strong className="block truncate text-sm">{m.name}</strong>
                    <small className="text-xs text-[var(--muted)]">
                      {m.role}
                      {m.vision ? " · vision" : ""}
                      {m.image_gen ? " · image generation" : ""}
                    </small>
                  </div>
                </li>
              ))}
              {liveModels.length === 0 ? <li className={hint}>No models available yet.</li> : null}
            </ul>
            <p className={hint}>The model route is chosen automatically; override it from the top bar in a chat.</p>
          </section>

          {/* Agents (specialists) */}
          <section className={panel}>
            <div className="flex items-center justify-between gap-3">
              <h4 className={heading}>Agents</h4>
              {isAdmin ? (
                <button type="button" className="btn ghost sm" onClick={() => setEditing(null)}>
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
                      <small className="block truncate text-xs text-[var(--muted)]">{s.covers}</small>
                    </span>
                  </span>
                  <button type="button" className="btn ghost sm" onClick={() => setEditing(s)}>
                    {isAdmin ? "Edit" : "View"}
                  </button>
                </li>
              ))}
            </ul>
            {isAdmin ? (
              <button
                type="button"
                className="btn ghost full"
                onClick={() => {
                  if (confirm("Restore the default IT, Development and Marketing agents?")) void specialists.reset();
                }}
              >
                Restore defaults
              </button>
            ) : null}
          </section>
        </div>
      </div>

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
