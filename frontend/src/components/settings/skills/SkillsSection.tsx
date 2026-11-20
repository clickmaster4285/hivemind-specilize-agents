"use client";

import { useRef, useState } from "react";

import SkillModal from "@/components/settings/skills/SkillModal";
import type { SkillsApi } from "@/hooks/useSkills";
import type { SpecialistsApi } from "@/hooks/useSpecialists";
import { downloadSkill, listSkillCatalog } from "@/lib/api";
import { errorText } from "@/lib/chat";
import { saveUrl } from "@/lib/download";
import type { CatalogSkill, Skill, SkillCategory } from "@/lib/types";
import { btn, formFieldCompact } from "@/lib/ui";

// Inline Tailwind building blocks (styling stays with the component), matching the other Settings sections.
const heading = "text-base font-semibold [font-family:var(--font-display)]";
const hint = "text-xs leading-relaxed text-muted";
// a skill card in the two-column grid (the user's ask, 2026-11-17): the whole card opens the skill
const card =
  "group relative flex min-h-[7.5rem] cursor-pointer flex-col gap-1.5 rounded-xl border border-line bg-panel-2 p-3.5 pr-10 hover:border-[color-mix(in_srgb,var(--copper)_45%,var(--line))]";
const grid = "m-0 grid list-none grid-cols-2 gap-3 p-0 max-[47.5rem]:grid-cols-1";
const chip = "rounded-full border border-line px-2 py-0.5 text-[0.68rem] text-muted";
const menuRow = "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[0.8rem] text-text hover:bg-hover disabled:opacity-50 disabled:hover:bg-transparent";
/** The ⋮ menu's width, to open it leftwards from the button so it stays inside the card's column. */
const MENU_WIDTH_PX = 176;
const pill = (on: boolean) =>
  `rounded-full border px-3 py-1 text-xs ${
    on
      ? "border-copper bg-[color-mix(in_srgb,var(--copper)_14%,transparent)] text-text"
      : "border-line text-muted hover:text-text"
  }`;

type View = "yours" | "discover";
/** undefined = closed; skill null = a new one */
type Editing = { skill: Skill | null; readOnly: boolean } | undefined;

/** One action in a card's ⋮ menu. */
type Action = { label: string; onClick: () => void; danger?: boolean; disabled?: boolean; title?: string };

/** The ⋮ button and its menu: every action of a card in one place, so the cards stay calm. */
function CardMenu({ actions }: { actions: Action[] }) {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  return (
    <>
      <button
        type="button"
        className="absolute right-2 top-2 flex h-7 w-7 items-center justify-center rounded-md text-muted hover:bg-hover hover:text-text"
        title="Actions"
        aria-label="Actions"
        aria-haspopup="menu"
        aria-expanded={at !== null}
        onClick={(e) => {
          e.stopPropagation();
          const r = e.currentTarget.getBoundingClientRect();
          setAt(at ? null : { x: r.right, y: r.bottom });
        }}
      >
        <svg width="15" height="15" viewBox="0 0 24 24" aria-hidden>
          <circle cx="12" cy="5" r="1.7" fill="currentColor" />
          <circle cx="12" cy="12" r="1.7" fill="currentColor" />
          <circle cx="12" cy="19" r="1.7" fill="currentColor" />
        </svg>
      </button>
      {at ? (
        <>
          <button
            type="button"
            className="fixed inset-0 z-40 cursor-default"
            aria-hidden
            tabIndex={-1}
            onClick={(e) => {
              e.stopPropagation();
              setAt(null);
            }}
          />
          <div
            role="menu"
            className="fixed z-50 rounded-lg border border-line bg-panel p-1 shadow-shadow"
            style={{ top: at.y + 4, left: Math.max(8, at.x - MENU_WIDTH_PX), width: MENU_WIDTH_PX }}
            onClick={(e) => e.stopPropagation()}
          >
            {actions.map((a) => (
              <button
                key={a.label}
                type="button"
                role="menuitem"
                disabled={a.disabled}
                title={a.title}
                className={`${menuRow} ${a.danger ? "text-danger-text" : ""}`}
                onClick={() => {
                  setAt(null);
                  a.onClick();
                }}
              >
                {a.label}
              </button>
            ))}
          </div>
        </>
      ) : null}
    </>
  );
}

/** One skill as a card: title, /name, what it does (two lines), its category and state; ⋮ holds the actions. */
function SkillCard({
  skill,
  actions,
  onOpen,
  badge,
  toggle,
}: {
  skill: Skill;
  actions: Action[];
  onOpen: () => void;
  badge?: string;
  toggle?: { on: boolean; disabled: boolean; onChange: (on: boolean) => void };
}) {
  return (
    <li
      className={`${card} ${skill.enabled ? "" : "opacity-60"}`}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter") onOpen();
      }}
      tabIndex={0}
      aria-label={`${skill.title}, /${skill.name}`}
    >
      <span className="block truncate text-sm">
        <strong>{skill.title}</strong> <span className="text-muted">/{skill.name}</span>
      </span>
      <small className="line-clamp-2 text-xs leading-relaxed text-muted">{skill.description}</small>
      <span className="mt-auto flex items-center gap-2 pt-1">
        {skill.category ? <span className={chip}>{skill.category}</span> : null}
        {badge ? <span className={chip}>{badge}</span> : null}
        {toggle ? (
          <label
            className="ml-auto flex items-center gap-1.5 text-xs text-muted"
            title="On: /name runs it"
            onClick={(e) => e.stopPropagation()}
          >
            <input
              type="checkbox"
              role="switch"
              checked={toggle.on}
              disabled={toggle.disabled}
              onChange={(e) => toggle.onChange(e.target.checked)}
            />
            {toggle.on ? "On" : "Off"}
          </label>
        ) : null}
      </span>
      <CardMenu actions={actions} />
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
    const open = () => setEditing({ skill: s, readOnly: !canChange(s) });
    const actions: Action[] = [
      { label: canChange(s) ? "Edit" : "View", onClick: open },
      {
        label: "Download SKILL.md",
        onClick: () =>
          void attempt(async () => {
            const file = await downloadSkill(id);
            saveUrl(file.url, file.filename);
          }),
      },
      ...(canChange(s)
        ? [{
            label: "Delete",
            danger: true,
            onClick: () => {
              if (confirm(`Delete the skill “${s.title}”?`)) void attempt(() => skills.remove(id));
            },
          }]
        : []),
    ];
    return (
      <SkillCard
        key={id}
        skill={s}
        actions={actions}
        onOpen={open}
        badge={s.team ? "Team" : undefined}
        toggle={{ on: s.enabled, disabled: !canChange(s), onChange: (on) => void attempt(() => skills.setEnabled(id, on)) }}
      />
    );
  }

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h4 className={heading}>Skills</h4>
        <div className="flex flex-wrap items-center gap-2">
          {canManageTeam ? (
            <label className="flex items-center gap-1.5 text-xs text-muted" title="Where + Add and Upload put a skill">
              <input type="checkbox" checked={forTeam} onChange={(e) => setForTeam(e.target.checked)} />
              Add for the whole team
            </label>
          ) : null}
          <button type="button" className={btn("ghost", "sm")} onClick={() => uploadRef.current?.click()}>
            Upload SKILL.md
          </button>
          <button type="button" className={btn("ghost", "sm")} onClick={() => setEditing({ skill: null, readOnly: false })}>
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

      <div className="flex gap-1 border-b border-line">
        {(["yours", "discover"] as const).map((v) => (
          <button
            key={v}
            type="button"
            onClick={() => show(v)}
            aria-current={view === v ? "page" : undefined}
            className={`-mb-px border-b-2 px-3 py-1.5 text-sm capitalize ${
              view === v ? "border-copper text-text" : "border-transparent text-muted hover:text-text"
            }`}
          >
            {v}
          </button>
        ))}
      </div>

      {error ? <p className="text-xs text-danger">{error}</p> : null}

      {view === "yours" ? (
        <>
          <h5 className="text-sm font-semibold">Team</h5>
          <ul className={grid}>
            {team.map(yoursRow)}
            {team.length === 0 ? (
              <li className={hint}>
                No team skills yet.{canManageTeam ? " Add one from Discover with “Add for the whole team” ticked." : ""}
              </li>
            ) : null}
          </ul>
          <h5 className="text-sm font-semibold">Created by you</h5>
          <ul className={grid}>
            {mine.map(yoursRow)}
            {mine.length === 0 ? (
              <li className={hint}>None yet — create one, upload a SKILL.md, or add one from Discover.</li>
            ) : null}
          </ul>
        </>
      ) : (
        <>
          <input
            className={formFieldCompact}
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
          <ul className={grid}>
            {found.map((s) => (
              <SkillCard
                key={s.name}
                skill={s}
                onOpen={() => setEditing({ skill: s, readOnly: true })}
                badge={s.added ? "Added ✓" : undefined}
                actions={[
                  { label: "View", onClick: () => setEditing({ skill: s, readOnly: true }) },
                  {
                    label: s.added ? "Already in Yours" : forTeam ? "Add for the whole team" : "Add to your skills",
                    disabled: s.added,
                    onClick: () =>
                      void attempt(async () => {
                        await skills.add(s.name, forTeam);
                        await fetchCatalog();
                      }),
                  },
                ]}
              />
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
