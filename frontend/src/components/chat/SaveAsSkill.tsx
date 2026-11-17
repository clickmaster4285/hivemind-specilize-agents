"use client";

/** "Save as skill" under an answer (BACKLOG L26 H2): the model drafts a reusable skill from what this job did — the
 *  steps, the tools, the shape of the answer, what to ask for next time — and the skill editor opens with it. Nothing
 *  is saved until the user presses Save there; then /name runs it again. */
import { useState } from "react";

import { useApp } from "@/components/app/AppProvider";
import SkillModal from "@/components/settings/skills/SkillModal";
import { draftSkill } from "@/lib/api";
import { errorText } from "@/lib/chat";
import type { SkillInput } from "@/lib/types";

const icon = (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 3l1.9 4.6L18.5 9l-4.6 1.9L12 15.5l-1.9-4.6L5.5 9l4.6-1.4z" /><path d="M19 14l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z" />
  </svg>
);

export default function SaveAsSkill({ jobId }: { jobId: string }) {
  const { auth, skills, specialists } = useApp();
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<SkillInput | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  if (!auth.user?.modules?.includes("skills")) return null;

  async function start() {
    setBusy(true);
    setSaved(null);
    try {
      const d = await draftSkill(jobId);
      setDraft({
        name: d.name, title: d.title, description: d.description, instructions: d.instructions,
        category: d.category, specialist: d.specialist, tools: d.tools, inputs: d.inputs, enabled: true,
      });
    } catch (err) {
      alert(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        title={busy ? "Drafting a skill from this answer…" : "Save as skill — reuse these steps with /name"}
        aria-label="Save as skill"
        disabled={busy}
        onClick={() => void start()}
        className="inline-flex h-[1.9rem] items-center justify-center gap-1 rounded-[0.4rem] px-[0.35rem] text-muted hover:bg-panel-2 hover:text-text disabled:opacity-60"
      >
        {icon}
        {busy ? <span className="text-[0.72rem]">Drafting…</span> : null}
      </button>
      {saved ? <span className="ml-[0.3rem] text-[0.72rem] text-muted">Saved — type /{saved} to run it.</span> : null}
      {draft ? (
        <SkillModal
          skill={null}
          draft={draft}
          readOnly={false}
          canChooseTeam={skills.canManageTeam}
          specialists={specialists.specialists}
          options={specialists.options}
          categories={[...new Set(skills.skills.map((s) => s.category))].filter(Boolean)}
          onSave={async (form, team) => {
            await skills.save(null, form, team);
            setSaved(form.name);
          }}
          onClose={() => setDraft(null)}
        />
      ) : null}
    </>
  );
}
