"use client";

import { useParams, useRouter } from "next/navigation";

import { useApp } from "@/components/app/AppProvider";
import { PAGE_WIDTH } from "@/components/app/PageHeader";
import SpecialistForm from "@/components/settings/SpecialistForm";
import { settingsTabPath } from "@/lib/routes";

const page = `mx-auto w-full ${PAGE_WIDTH} px-4 py-8`;
const panel = "rounded-xl border border-line bg-panel p-5";

/** /settings/agents/<id> (BACKLOG L19b D5): one agent's editor as its own page; /settings/agents/new makes one
 *  (admins). Members see it read-only. Leaving it returns to Settings → Agents. */
export default function AgentPage() {
  const app = useApp();
  const router = useRouter();
  const { id: raw } = useParams<{ id: string }>();
  const id = decodeURIComponent(raw);
  const { specialists, loaded, options, save, remove } = app.specialists;
  const back = () => router.push(settingsTabPath("agents"));

  if (!app.auth.user) return null; // the shell shows the login when signed out
  const isAdmin = app.auth.user.role === "admin";
  const creating = id === "new";
  if (creating && !isAdmin) return <p className={`${page} text-[0.9rem] text-muted`}>Only admins add agents.</p>;
  if (!creating && !loaded) return <p className={`${page} text-[0.9rem] text-muted`}>Loading…</p>;

  const specialist = creating ? null : specialists.find((s) => s.id === id);
  if (specialist === undefined) {
    return (
      <div className={page}>
        <p className="text-[0.9rem] text-muted">This agent no longer exists.</p>
        <button type="button" className="mt-3 text-sm text-copper underline" onClick={back}>
          ← Agents
        </button>
      </div>
    );
  }
  return (
    <div className={page}>
      <section className={panel}>
        <SpecialistForm
          key={id}
          specialist={specialist}
          options={options}
          readOnly={!isAdmin}
          onSave={save}
          onDelete={remove}
          onDone={back}
        />
      </section>
    </div>
  );
}
