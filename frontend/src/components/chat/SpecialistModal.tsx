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
    <div className="modal-backdrop" onClick={onClose}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={save}>
        <header>
          <h3>{id ? form.name || "Specialist" : "New specialist"}</h3>
          <button type="button" className="icon-btn" onClick={onClose}>
            ×
          </button>
        </header>
        <label>
          Name
          <input required value={form.name} onChange={(e) => set({ name: e.target.value })} />
        </label>
        <label>
          Covers — the assistant picks this specialist from this line
          <textarea required rows={3} value={form.covers} onChange={(e) => set({ covers: e.target.value })} />
        </label>
        <label>
          Instructions — how it answers
          <textarea rows={8} value={form.instructions} onChange={(e) => set({ instructions: e.target.value })} />
        </label>
        <fieldset className="check-grid">
          <legend>Tools it may use</legend>
          {options.tools.map((tool) => (
            <label key={tool.name} className="check" title={tool.description}>
              <input
                type="checkbox"
                checked={form.tools.includes(tool.name)}
                onChange={(e) => set({ tools: toggleIn(form.tools, tool.name, e.target.checked) })}
              />
              {tool.name.replace(/_/g, " ")}
            </label>
          ))}
        </fieldset>
        <fieldset className="check-grid">
          <legend>Knowledge collections searched first</legend>
          {options.collections.map((c) => (
            <label key={c} className="check">
              <input
                type="checkbox"
                checked={form.collections.includes(c)}
                onChange={(e) => set({ collections: toggleIn(form.collections, c, e.target.checked) })}
              />
              {c}
            </label>
          ))}
        </fieldset>
        <label>
          Colour
          <input type="color" value={form.accent} onChange={(e) => set({ accent: e.target.value })} />
        </label>
        <label className="check">
          <input type="checkbox" checked={form.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
          Enabled
        </label>
        <footer>
          {id ? (
            <button type="button" className="btn danger ghost" onClick={() => void remove()}>
              Delete
            </button>
          ) : (
            <span />
          )}
          <div className="footer-right">
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
