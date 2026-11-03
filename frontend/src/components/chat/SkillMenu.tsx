"use client";

import { useState, type KeyboardEvent } from "react";

import type { Skill } from "@/lib/types";

/** While the message is just "/" plus the start of a name, the skills it could be (BACKLOG L14). */
const SLASH = /^\/([a-z0-9-]*)$/i;

/**
 * The "/" menu's state: which skills match what is typed, which one is highlighted, and the keys that drive it
 * (↑ ↓ to move, Enter or Tab to pick, Esc to close). `onKey` returns true when it used the key.
 */
export function useSkillMenu(query: string, skills: Skill[], onPick: (skill: Skill) => void) {
  const [index, setIndex] = useState(0);
  const [closedFor, setClosedFor] = useState<string | null>(null);
  const typed = SLASH.exec(query)?.[1]?.toLowerCase();
  // names starting with what is typed first, then any name or title containing it
  const starts = (s: Skill) => s.name.startsWith(typed ?? "");
  const matches =
    typed === undefined
      ? []
      : [
          ...skills.filter(starts),
          ...skills.filter((s) => !starts(s) && `${s.name} ${s.title.toLowerCase()}`.includes(typed)),
        ];
  const open = matches.length > 0 && closedFor !== query;
  const active = Math.min(index, matches.length - 1);

  function pick(skill: Skill) {
    setIndex(0);
    onPick(skill);
  }

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>): boolean {
    if (!open) return false;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const step = event.key === "ArrowDown" ? 1 : -1;
      setIndex((active + step + matches.length) % matches.length);
    } else if (event.key === "Enter" || event.key === "Tab") {
      pick(matches[active]);
    } else if (event.key === "Escape") {
      setClosedFor(query);
    } else {
      return false;
    }
    event.preventDefault();
    return true;
  }

  return { open, matches, active, pick, onKey };
}

/** The list above the message box: each skill's title, /name and what it does. */
export default function SkillMenu({ menu }: { menu: ReturnType<typeof useSkillMenu> }) {
  if (!menu.open) return null;
  return (
    <div
      role="listbox"
      aria-label="Skills"
      className="absolute bottom-full left-0 right-0 z-50 mb-2 max-h-[18rem] overflow-y-auto rounded-xl border border-[var(--line)] bg-[var(--panel)] p-1.5 shadow-[var(--shadow)]"
    >
      <div className="px-2.5 pb-1 pt-1 text-[0.7rem] font-medium uppercase tracking-[0.05em] text-[var(--muted)]">
        Skills
      </div>
      {menu.matches.map((skill, i) => (
        <button
          key={skill.name}
          type="button"
          role="option"
          aria-selected={i === menu.active}
          // mousedown, not click: the message box keeps its focus
          onMouseDown={(e) => {
            e.preventDefault();
            menu.pick(skill);
          }}
          className={`flex w-full flex-col items-start gap-[0.1rem] rounded-lg px-2.5 py-1.5 text-left ${
            i === menu.active ? "bg-[var(--hover)]" : "hover:bg-[var(--hover)]"
          }`}
        >
          <span className="text-[0.85rem] text-[var(--text)]">
            <span className="font-semibold">{skill.title}</span> <span className="text-[var(--muted)]">/{skill.name}</span>
            <span className="ml-1.5 text-[0.68rem] text-[var(--muted)]">
              {skill.id === null ? "· built-in" : skill.team ? "· team" : "· yours"}
            </span>
          </span>
          <span className="line-clamp-1 text-[0.74rem] text-[var(--muted)]">{skill.description}</span>
        </button>
      ))}
    </div>
  );
}
