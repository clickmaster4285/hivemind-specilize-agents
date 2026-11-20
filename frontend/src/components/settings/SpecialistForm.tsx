"use client";

import { useState, type FormEvent } from "react";

import { errorText, toggleIn } from "@/lib/chat";
import type { Specialist, SpecialistInput, SpecialistOptions } from "@/lib/types";
import { btn, formField, formLabel } from "@/lib/ui";

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
  /** members may look but not change (the API refuses their saves) */
  readOnly: boolean;
  onSave: (id: string | null, form: SpecialistInput) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onDone: () => void;
};

// Inline Tailwind building blocks (shared classes — btn, formField — stay global utilities).
const checkRow = "flex items-center gap-[0.45rem] text-text";
const fieldset =
  "m-0 grid gap-x-[0.8rem] gap-y-[0.35rem] rounded-[0.65rem] border border-line px-[0.7rem] py-[0.6rem] [grid-template-columns:repeat(auto-fill,minmax(10rem,1fr))]";

/** An agent's own page body (/settings/agents/<id>, BACKLOG L19b D5; was a modal): covers line, instructions,
 *  allowed tools, knowledge collections. The instructions get room to be read and written on a full page. */
export default function SpecialistForm({ specialist, options, readOnly, onSave, onDelete, onDone }: Props) {
  const [form, setForm] = useState<SpecialistInput>(() => formFrom(specialist));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (patch: Partial<SpecialistInput>) => setForm((f) => ({ ...f, ...patch }));
  const id = specialist?.id ?? null;

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onDone();
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  function save(event: FormEvent) {
    event.preventDefault();
    void run(() => onSave(id, form));
  }

  function remove() {
    if (!id || !confirm(`Delete the ${form.name || "agent"} agent?`)) return;
    void run(() => onDelete(id));
  }

  return (
    <form className="flex flex-col gap-4" onSubmit={save}>
      <header className="flex items-center gap-3">
        <button type="button" className={btn("ghost", "sm")} onClick={onDone} disabled={busy}>
          ← Agents
        </button>
        <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: form.accent }} />
        <h4 className="m-0 truncate text-base font-semibold [font-family:var(--font-display)]">
          {id ? form.name || "Agent" : "New agent"}
          {readOnly ? <span className="ml-2 text-xs font-normal text-muted">view only</span> : null}
        </h4>
      </header>

      {/* a disabled fieldset turns every field inside read-only for members */}
      <fieldset disabled={readOnly || busy} className="m-0 flex min-w-0 flex-col gap-3 border-0 p-0">
        <label className={formLabel}>
          Name
          <input className={formField} required value={form.name} onChange={(e) => set({ name: e.target.value })} />
        </label>
        <label className={formLabel}>
          Covers — the assistant picks this agent from this line
          <textarea
            className={formField}
            required
            rows={3}
            value={form.covers}
            onChange={(e) => set({ covers: e.target.value })}
          />
        </label>
        <label className={formLabel}>
          Instructions — how it answers
          <textarea
            className={`${formField} resize-y`}
            rows={14}
            value={form.instructions}
            onChange={(e) => set({ instructions: e.target.value })}
          />
        </label>
        <fieldset className={fieldset}>
          <legend className="px-[0.3rem] text-[0.85rem] text-muted">Tools it may use</legend>
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
          <legend className="px-[0.3rem] text-[0.85rem] text-muted">Knowledge collections searched first</legend>
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
        <div className="flex flex-wrap items-end gap-6">
          <label className={formLabel}>
            Colour
            <input
              className="h-9 w-16 cursor-pointer rounded-[0.55rem] border border-line bg-panel-2"
              type="color"
              value={form.accent}
              onChange={(e) => set({ accent: e.target.value })}
            />
          </label>
          <label className={`${checkRow} pb-2`}>
            <input type="checkbox" checked={form.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
            Enabled
          </label>
        </div>
      </fieldset>

      {error ? <p className="m-0 text-xs text-danger">{error}</p> : null}

      <footer className="flex items-center justify-between gap-3">
        {id && !readOnly ? (
          <button type="button" className={btn("danger", "ghost")} onClick={remove} disabled={busy}>
            Delete
          </button>
        ) : (
          <span />
        )}
        <div className="ml-auto flex gap-2">
          <button type="button" className={btn("ghost")} onClick={onDone} disabled={busy}>
            {readOnly ? "Back" : "Cancel"}
          </button>
          {readOnly ? null : (
            <button type="submit" className={btn("primary")} disabled={busy}>
              {busy ? "Saving…" : "Save agent"}
            </button>
          )}
        </div>
      </footer>
    </form>
  );
}
