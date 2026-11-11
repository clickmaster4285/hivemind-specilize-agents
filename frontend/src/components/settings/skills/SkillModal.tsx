"use client";

import { useState, type FormEvent } from "react";

import { errorText, toggleIn } from "@/lib/chat";
import type { Skill, SkillInput, Specialist, SpecialistOptions } from "@/lib/types";
import { btn, iconBtn } from "@/lib/ui";

const emptyForm: SkillInput = {
  name: "",
  title: "",
  description: "",
  instructions: "",
  category: "",
  specialist: "",
  tools: [],
  inputs: [],
  enabled: true,
};

function formFrom(s: Skill | null): SkillInput {
  if (!s) return emptyForm;
  return {
    name: s.name,
    title: s.title,
    description: s.description,
    instructions: s.instructions,
    category: s.category,
    specialist: s.specialist,
    tools: [...s.tools],
    inputs: [...s.inputs],
    enabled: s.enabled,
  };
}

/** "Monthly SEO check" → "monthly-seo-check" (the /name the server accepts). */
const nameFrom = (title: string) =>
  title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");

type Props = {
  /** null = a new skill */
  skill: Skill | null;
  /** a built-in skill (Discover's preview) or a team skill seen by a member */
  readOnly: boolean;
  /** an admin creating a skill: for the team or only for them */
  canChooseTeam: boolean;
  specialists: Specialist[];
  options: SpecialistOptions;
  categories: string[];
  onSave: (form: SkillInput, team: boolean) => Promise<void>;
  onClose: () => void;
};

// Inline Tailwind building blocks, matching SpecialistModal.
const field =
  "w-full rounded-[0.55rem] border border-line bg-panel-2 px-[0.7rem] py-[0.55rem] font-[inherit] text-text outline-none resize-y disabled:opacity-80";
const labelCol = "flex flex-col gap-1 text-[0.82rem] text-muted";
const checkRow = "flex items-center gap-[0.45rem] text-text";
const toolGrid =
  "m-0 grid gap-x-[0.8rem] gap-y-[0.35rem] rounded-[0.65rem] border border-line px-[0.7rem] py-[0.6rem] [grid-template-columns:repeat(auto-fill,minmax(10rem,1fr))]";

/** Create, edit or view a skill: what it is for, who answers, what it asks for, its tools and its steps. */
export default function SkillModal(props: Props) {
  const { skill, readOnly, canChooseTeam, specialists, options, categories, onSave, onClose } = props;
  const [form, setForm] = useState<SkillInput>(() => formFrom(skill));
  const [inputsText, setInputsText] = useState(() => (skill?.inputs ?? []).join("\n"));
  const [nameTouched, setNameTouched] = useState(Boolean(skill));
  const [team, setTeam] = useState(false);
  const [busy, setBusy] = useState(false);
  const set = (patch: Partial<SkillInput>) => setForm((f) => ({ ...f, ...patch }));
  const isNew = skill === null;

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const inputs = inputsText.split("\n").map((s) => s.trim()).filter(Boolean);
      await onSave({ ...form, inputs }, team);
      onClose();
    } catch (err) {
      alert(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-backdrop p-4 backdrop-blur-[4px]"
      onClick={onClose}
    >
      <form
        className="flex max-h-[92vh] w-[min(680px,100%)] flex-col gap-3 overflow-y-auto rounded-2xl border border-line bg-panel px-[1.1rem] pt-4 pb-[1.1rem] shadow-shadow"
        onClick={(e) => e.stopPropagation()}
        onSubmit={save}
      >
        <header className="flex items-center justify-between gap-3">
          <h3 className="m-0 text-[1.15rem] [font-family:var(--font-display)]">
            {isNew ? "New skill" : form.title || form.name}
            {!isNew ? <span className="ml-2 text-[0.85rem] text-muted">/{form.name}</span> : null}
          </h3>
          <button type="button" className={iconBtn()} onClick={onClose}>
            ×
          </button>
        </header>

        {/* disabled fieldset: a preview shows every field without letting it change */}
        <fieldset disabled={readOnly} className="m-0 flex min-w-0 flex-col gap-3 border-0 p-0">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className={labelCol}>
              Title
              <input
                className={field}
                required
                value={form.title}
                onChange={(e) =>
                  set({ title: e.target.value, ...(nameTouched ? {} : { name: nameFrom(e.target.value) }) })
                }
              />
            </label>
            <label className={labelCol}>
              Name — typed as /name in the chat
              <input
                className={field}
                required
                value={form.name}
                onChange={(e) => {
                  setNameTouched(true);
                  set({ name: e.target.value });
                }}
              />
            </label>
          </div>
          <label className={labelCol}>
            What it does and when to use it
            <textarea
              className={field}
              required
              rows={2}
              value={form.description}
              onChange={(e) => set({ description: e.target.value })}
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className={labelCol}>
              Category
              <input
                className={field}
                list="skill-categories"
                value={form.category}
                onChange={(e) => set({ category: e.target.value })}
              />
              <datalist id="skill-categories">
                {categories.map((c) => (
                  <option key={c} value={c} />
                ))}
              </datalist>
            </label>
            <label className={labelCol}>
              Who answers
              <select className={field} value={form.specialist} onChange={(e) => set({ specialist: e.target.value })}>
                <option value="">The usual pick</option>
                {specialists.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className={labelCol}>
            What it needs from the user — one per line (asked for first when the message lacks it)
            <textarea className={field} rows={3} value={inputsText} onChange={(e) => setInputsText(e.target.value)} />
          </label>
          <fieldset className={toolGrid}>
            <legend className="px-[0.3rem] text-[0.85rem] text-muted">
              Tools it may use (only those the answering specialist may use are offered)
            </legend>
            {options.tools.map((tool) => (
              <label key={tool.name} className={checkRow} title={tool.description}>
                <input
                  type="checkbox"
                  checked={form.tools.includes(tool.name)}
                  onChange={(e) => set({ tools: toggleIn(form.tools, tool.name, e.target.checked) })}
                />
                {tool.name.replace(/_/g, " ")}
              </label>
            ))}
          </fieldset>
          <label className={labelCol}>
            Instructions — the steps and the output format (Markdown)
            <textarea
              className={`${field} [font-family:var(--font-mono)] text-[0.8rem]`}
              required
              rows={12}
              value={form.instructions}
              onChange={(e) => set({ instructions: e.target.value })}
            />
          </label>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            <label className={checkRow}>
              <input type="checkbox" checked={form.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
              On — can be run with /{form.name || "name"}
            </label>
            {isNew && canChooseTeam ? (
              <label className={checkRow}>
                <input type="checkbox" checked={team} onChange={(e) => setTeam(e.target.checked)} />
                For the whole team
              </label>
            ) : null}
          </div>
        </fieldset>

        <footer className="flex justify-end gap-2">
          <button type="button" className={btn("ghost")} onClick={onClose}>
            {readOnly ? "Close" : "Cancel"}
          </button>
          {readOnly ? null : (
            <button type="submit" className={btn("primary")} disabled={busy}>
              {busy ? "Saving…" : "Save"}
            </button>
          )}
        </footer>
      </form>
    </div>
  );
}
