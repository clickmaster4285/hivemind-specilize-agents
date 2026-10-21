"use client";

import { useState, type FormEvent } from "react";

import { errorText, toggleIn } from "@/lib/chat";
import type { Specialist, SpecialistInput, SpecialistOptions } from "@/lib/types";

const emptyForm: SpecialistInput = {
  name: "",
  covers: "",
  instructions: "",
  tools: [],
  collections: ["general"],
  enabled: true,
  accent: "#6b8cae",
};

function formFrom(s: Specialist | null): SpecialistInput {
  if (!s) return emptyForm;
  return {
    name: s.name,
    covers: s.covers,
    instructions: s.instructions,
    tools: [...s.tools],
    collections: [...s.collections],
    enabled: s.enabled,
    accent: s.accent || "#6b8cae",
  };
}

type Props = {
  /** null = a new specialist */
  specialist: Specialist | null;
  options: SpecialistOptions;
  onSave: (id: string | null, form: SpecialistInput) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onClose: () => void;
};

// Inline Tailwind building blocks (shared classes — icon-btn, btn — stay global utilities).
const field =
  "w-full rounded-[0.55rem] border border-[var(--line)] bg-[var(--panel-2)] px-[0.7rem] py-[0.55rem] font-[inherit] text-[var(--text)] outline-none resize-y";
const labelCol = "flex flex-col gap-1 text-[0.82rem] text-[var(--muted)]";
const checkRow = "flex items-center gap-[0.45rem] text-[var(--text)]";
const fieldset =
  "m-0 grid gap-x-[0.8rem] gap-y-[0.35rem] rounded-[0.65rem] border border-[var(--line)] px-[0.7rem] py-[0.6rem] [grid-template-columns:repeat(auto-fill,minmax(10rem,1fr))]";

/** Create or edit a specialist: covers line, instructions, allowed tools, knowledge collections. */
export default function SpecialistModal({ specialist, options, onSave, onDelete, onClose }: Props) {
  const [form, setForm] = useState<SpecialistInput>(() => formFrom(specialist));
  const set = (patch: Partial<SpecialistInput>) => setForm((f) => ({ ...f, ...patch }));
  const id = specialist?.id ?? null;

  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      await onSave(id, form);
      onClose();
    } catch (err) {
      alert(errorText(err));
    }
  }

  async function remove() {
    if (!id || !confirm("Delete this specialist?")) return;
    try {
      await onDelete(id);
      onClose();
    } catch (err) {
      alert(errorText(err));
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-[rgba(6,8,12,0.72)] p-4 backdrop-blur-[4px]"
      onClick={onClose}
    >
      <form
        className="flex w-[min(560px,100%)] flex-col gap-3 rounded-2xl border border-[var(--line)] bg-[var(--panel)] px-[1.1rem] pt-4 pb-[1.1rem] shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
        onSubmit={save}
      >
        <header className="flex items-center justify-between gap-3">
          <h3 className="m-0 text-[1.15rem] [font-family:var(--font-display)]">
            {id ? form.name || "Specialist" : "New specialist"}
          </h3>
          <button type="button" className="icon-btn" onClick={onClose}>
            ×
          </button>
        </header>
        <label className={labelCol}>
          Name
          <input className={field} required value={form.name} onChange={(e) => set({ name: e.target.value })} />
        </label>
        <label className={labelCol}>
          Covers — the assistant picks this specialist from this line
          <textarea
            className={field}
            required
            rows={3}
            value={form.covers}
            onChange={(e) => set({ covers: e.target.value })}
          />
        </label>
        <label className={labelCol}>
          Instructions — how it answers
          <textarea
            className={field}
            rows={8}
            value={form.instructions}
            onChange={(e) => set({ instructions: e.target.value })}
          />
        </label>
        <fieldset className={fieldset}>
          <legend className="px-[0.3rem] text-[0.85rem] text-[var(--muted)]">Tools it may use</legend>
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
        <fieldset className={fieldset}>
          <legend className="px-[0.3rem] text-[0.85rem] text-[var(--muted)]">Knowledge collections searched first</legend>
          {options.collections.map((c) => (
            <label key={c} className={checkRow}>
              <input
                type="checkbox"
                checked={form.collections.includes(c)}
                onChange={(e) => set({ collections: toggleIn(form.collections, c, e.target.checked) })}
              />
              {c}
            </label>
          ))}
        </fieldset>
        <label className={labelCol}>
          Colour
          <input
            className="h-9 w-16 cursor-pointer rounded-[0.55rem] border border-[var(--line)] bg-[var(--panel-2)]"
            type="color"
            value={form.accent}
            onChange={(e) => set({ accent: e.target.value })}
          />
        </label>
        <label className={checkRow}>
          <input type="checkbox" checked={form.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
          Enabled
        </label>
        <footer className="flex items-center justify-between gap-3">
          {id ? (
            <button type="button" className="btn danger ghost" onClick={() => void remove()}>
              Delete
            </button>
          ) : (
            <span />
          )}
          <div className="ml-auto flex gap-2">
            <button type="button" className="btn ghost" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn primary">
              Save specialist
            </button>
          </div>
        </footer>
      </form>
    </div>
  );
}
