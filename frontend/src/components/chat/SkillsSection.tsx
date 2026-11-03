"use client";

import { useRef, useState, type ReactNode } from "react";

import SkillModal from "@/components/chat/SkillModal";
import type { SkillsApi } from "@/hooks/useSkills";
import type { SpecialistsApi } from "@/hooks/useSpecialists";
import { downloadSkill, listSkillCatalog } from "@/lib/api";
import { errorText } from "@/lib/chat";
import { saveUrl } from "@/lib/download";
import type { CatalogSkill, Skill, SkillCategory } from "@/lib/types";

// Inline Tailwind building blocks (styling stays with the component), matching the other Settings sections.
const heading = "text-base font-semibold [font-family:var(--font-display)]";
const hint = "text-xs leading-relaxed text-[var(--muted)]";
const rowCard = "flex items-center justify-between gap-3 rounded-lg border border-[var(--line)] bg-[var(--panel-2)] px-3 py-2.5";
const field =
  "w-full rounded-lg border border-[var(--line)] bg-[var(--panel-2)] px-3 py-2 text-sm text-[var(--text)] placeholder:text-[var(--muted)] outline-none focus:border-[var(--copper)]";
const pill = (on: boolean) =>
  `rounded-full border px-3 py-1 text-xs ${
    on
      ? "border-[var(--copper)] bg-[color-mix(in_srgb,var(--copper)_14%,transparent)] text-[var(--text)]"
      : "border-[var(--line)] text-[var(--muted)] hover:text-[var(--text)]"
  }`;

type View = "yours" | "discover";
/** undefined = closed; skill null = a new one */
type Editing = { skill: Skill | null; readOnly: boolean } | undefined;

/** One skill's line: title, /name, what it does — and its actions on the right. */
function SkillRow({ skill, children }: { skill: Skill; children: ReactNode }) {
  return (
    <li className={`${rowCard} ${skill.enabled ? "" : "opacity-60"}`}>
      <span className="min-w-0">
        <span className="block truncate text-sm">
          <strong>{skill.title}</strong> <span className="text-[var(--muted)]">/{skill.name}</span>
        </span>
        <small className="line-clamp-1 text-xs text-[var(--muted)]">{skill.description}</small>
      </span>
      <span className="flex shrink-0 items-center gap-1.5">{children}</span>
    </li>
  );
}

/** Settings → Skills, like claude.ai's Customize: Yours (the team's and your own — switch on/off, edit, download,
 *  delete, create, upload a SKILL.md) and Discover (the built-in skills by category; "+" adds one to Yours to
 *  adapt it — built-in skills already run with /name without adding them). */
export default function SkillsSection({ skills, specialists }: { skills: SkillsApi; specialists: SpecialistsApi }) {
  const [view, setView] = useState<View>("yours");
  const [editing, setEditing] = useState<Editing>(undefined);
  const [forTeam, setForTeam] = useState(false);          // admins: where "+" and upload put a skill
  const [catalog, setCatalog] = useState<CatalogSkill[]>([]);
  const [categories, setCategories] = useState<SkillCategory[]>([]);
  const [category, setCategory] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const uploadRef = useRef<HTMLInputElement | null>(null);
  const canManageTeam = skills.canManageTeam;

  async function attempt(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function fetchCatalog() {
    const found = await listSkillCatalog();
    setCatalog(found.skills);
    setCategories(found.categories);
  }

  function show(next: View) {
    setView(next);
    if (next === "discover") void attempt(fetchCatalog);
  }

  const canChange = (s: Skill) => !s.team || canManageTeam;
  const team = skills.skills.filter((s) => s.team);
  const mine = skills.skills.filter((s) => !s.team);
  const needle = search.trim().toLowerCase();
  const found = catalog.filter(
    (s) =>
      (!category || s.category === category) &&
      (!needle || `${s.name} ${s.title} ${s.description}`.toLowerCase().includes(needle)),
  );

  function yoursRow(s: Skill) {
    const id = s.id as number;          // skills in Yours always have an id
    return (
      <SkillRow key={id} skill={s}>
        <label className="flex items-center gap-1 text-xs text-[var(--muted)]" title="On: /name runs it">
          <input
            type="checkbox"
            checked={s.enabled}
            disabled={!canChange(s)}
            onChange={(e) => void attempt(() => skills.setEnabled(id, e.target.checked))}
          />
          On
        </label>
        <button type="button" className="btn ghost sm" onClick={() => setEditing({ skill: s, readOnly: !canChange(s) })}>
          {canChange(s) ? "Edit" : "View"}
        </button>
        <button
          type="button"
          className="btn ghost sm"
          title="Download as SKILL.md"
          onClick={() =>
            void attempt(async () => {
              const file = await downloadSkill(id);
              saveUrl(file.url, file.filename);
            })
          }
        >
          ⬇
        </button>
        {canChange(s) ? (
          <button
            type="button"
            className="btn ghost sm"
            title="Delete"
            onClick={() => {
              if (confirm(`Delete the skill “${s.title}”?`)) void attempt(() => skills.remove(id));
            }}
          >
            ×
          </button>
        ) : null}
      </SkillRow>
    );
  }

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h4 className={heading}>Skills</h4>
        <div className="flex flex-wrap items-center gap-2">
          {canManageTeam ? (
            <label className="flex items-center gap-1.5 text-xs text-[var(--muted)]" title="Where + Add and Upload put a skill">
              <input type="checkbox" checked={forTeam} onChange={(e) => setForTeam(e.target.checked)} />
              Add for the whole team
            </label>
          ) : null}
          <button type="button" className="btn ghost sm" onClick={() => uploadRef.current?.click()}>
            Upload SKILL.md
          </button>
          <button type="button" className="btn ghost sm" onClick={() => setEditing({ skill: null, readOnly: false })}>
            New skill
          </button>
          <input
            ref={uploadRef}
            type="file"
            accept=".md,text/markdown,text/plain"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) void attempt(async () => skills.upload(await file.text(), forTeam));
            }}
          />
        </div>
      </div>
      <p className={hint}>
        A skill is a repeatable task — its steps, what it needs from you, who answers and the format of the result.
        Type <strong>/</strong> in the chat to run one. Built-in skills work straight away; add one to Yours to adapt
        it.
      </p>

      <div className="flex gap-1 border-b border-[var(--line)]">
        {(["yours", "discover"] as const).map((v) => (
          <button
            key={v}
            type="button"
            onClick={() => show(v)}
            aria-current={view === v ? "page" : undefined}
            className={`-mb-px border-b-2 px-3 py-1.5 text-sm capitalize ${
              view === v ? "border-[var(--copper)] text-[var(--text)]" : "border-transparent text-[var(--muted)] hover:text-[var(--text)]"
            }`}
          >
            {v}
          </button>
        ))}
      </div>

      {error ? <p className="text-xs text-[var(--danger)]">{error}</p> : null}

      {view === "yours" ? (
        <>
          <h5 className="text-sm font-semibold">Team</h5>
          <ul className="flex flex-col gap-2">
            {team.map(yoursRow)}
            {team.length === 0 ? (
              <li className={hint}>
                No team skills yet.{canManageTeam ? " Add one from Discover with “Add for the whole team” ticked." : ""}
              </li>
            ) : null}
          </ul>
          <h5 className="text-sm font-semibold">Created by you</h5>
          <ul className="flex flex-col gap-2">
            {mine.map(yoursRow)}
            {mine.length === 0 ? (
              <li className={hint}>None yet — create one, upload a SKILL.md, or add one from Discover.</li>
            ) : null}
          </ul>
        </>
      ) : (
        <>
          <input
            className={field}
            type="search"
            placeholder="Search skills"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="flex flex-wrap gap-1.5">
            <button type="button" className={pill(category === null)} onClick={() => setCategory(null)}>
              All {catalog.length}
            </button>
            {categories.map((c) => (
              <button key={c.name} type="button" className={pill(category === c.name)} onClick={() => setCategory(c.name)}>
                {c.name} {c.count}
              </button>
            ))}
          </div>
          <ul className="flex flex-col gap-2">
            {found.map((s) => (
              <SkillRow key={s.name} skill={s}>
                <span className="hidden text-xs text-[var(--muted)] sm:inline">{s.category}</span>
                <button type="button" className="btn ghost sm" onClick={() => setEditing({ skill: s, readOnly: true })}>
                  View
                </button>
                <button
                  type="button"
                  className="btn ghost sm"
                  disabled={s.added}
                  title={s.added ? "Already in Yours" : forTeam ? "Add for the whole team" : "Add to your skills"}
                  onClick={() =>
                    void attempt(async () => {
                      await skills.add(s.name, forTeam);
                      await fetchCatalog();
                    })
                  }
                >
                  {s.added ? "Added ✓" : "+ Add"}
                </button>
              </SkillRow>
            ))}
            {found.length === 0 ? <li className={hint}>No skill matches.</li> : null}
          </ul>
        </>
      )}

      {editing !== undefined ? (
        <SkillModal
          key={editing.skill?.id ?? editing.skill?.name ?? "new"}
          skill={editing.skill}
          readOnly={editing.readOnly}
          canChooseTeam={canManageTeam}
          specialists={specialists.specialists}
          options={specialists.options}
          categories={[...new Set([...categories.map((c) => c.name), ...skills.skills.map((s) => s.category)])].filter(Boolean)}
          onSave={(form, asTeam) => skills.save(editing.skill?.id ?? null, form, asTeam)}
          onClose={() => setEditing(undefined)}
        />
      ) : null}
    </>
  );
}
