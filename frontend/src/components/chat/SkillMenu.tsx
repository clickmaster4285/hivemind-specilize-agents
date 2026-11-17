"use client";

import { useState, type KeyboardEvent } from "react";

import { COMPACT_COMMAND } from "@/lib/chat";

import type { Skill } from "@/lib/types";

/** The "/name" being typed at the end of the message — at its start or after a space, so "/" works anywhere in
 *  the prompt, not only as its first character (BACKLOG L14). Group 1: what comes before it; group 2: the name. */
const SLASH_TOKEN = /^([\s\S]*?)(?:^|(?<=\s))\/([a-z0-9-]*)$/i;

/** Commands run by the app itself; listed with the skills so they can be found (O76's /compact was invisible). */
type Command = { name: string; title: string; description: string };
const COMMANDS: Command[] = [
  {
    name: COMPACT_COMMAND.slice(1),
    title: "Compact this chat",
    description: "Summarize the older messages now, so the chat has room again. Sent on its own, not with other text.",
  },
];

/** One row of the menu: a skill (applies to the message) or a command (the whole message). */
type Item = { kind: "skill"; skill: Skill } | { kind: "command"; command: Command };

const itemName = (item: Item) => (item.kind === "skill" ? item.skill.name : item.command.name);
const itemTitle = (item: Item) => (item.kind === "skill" ? item.skill.title : item.command.title);

/** Best matches first: names starting with what is typed, then any name or title containing it. */
function ranked(items: Item[], typed: string): Item[] {
  const starts = (it: Item) => itemName(it).startsWith(typed);
  return [
    ...items.filter(starts),
    ...items.filter((it) => !starts(it) && `${itemName(it)} ${itemTitle(it).toLowerCase()}`.includes(typed)),
  ];
}

/**
 * The "/" menu's state: which commands and skills match what is typed, which one is highlighted, and the keys that
 * drive it (↑ ↓ to move, Enter or Tab to pick, Esc to close). `onKey` returns true when it used the key.
 * Picking a skill puts "/name " at the start of the message (where the server reads it) and keeps the other text;
 * picking a command makes it the whole message.
 */
export function useSkillMenu(query: string, skills: Skill[], onQuery: (text: string) => void) {
  const [index, setIndex] = useState(0);
  const [closedFor, setClosedFor] = useState<string | null>(null);
  const token = SLASH_TOKEN.exec(query);
  const before = token?.[1] ?? "";
  const typed = token?.[2]?.toLowerCase();
  // a command is the whole message, so it is offered only when nothing else is written
  const commands: Item[] = before.trim() ? [] : COMMANDS.map((command) => ({ kind: "command", command }));
  const matches =
    typed === undefined ? [] : ranked([...commands, ...skills.map((skill): Item => ({ kind: "skill", skill }))], typed);
  // a command typed in full stays closed, so Enter sends it instead of picking it again
  const typedCommand = COMMANDS.some((c) => query.trim().toLowerCase() === `/${c.name}`);
  const open = matches.length > 0 && closedFor !== query && !typedCommand;
  const active = Math.min(index, matches.length - 1);

  function pick(item: Item) {
    setIndex(0);
    if (item.kind === "command") onQuery(`/${item.command.name}`);
    else onQuery(`/${item.skill.name} ${before.trimEnd()}`.trimEnd() + " ");
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

function tag(item: Item): string {
  if (item.kind === "command") return "· command";
  return item.skill.id === null ? "· built-in" : item.skill.team ? "· team" : "· yours";
}

/** The list above the message box: commands, then skills — each title, /name and what it does. */
export default function SkillMenu({ menu }: { menu: ReturnType<typeof useSkillMenu> }) {
  if (!menu.open) return null;
  return (
    <div
      role="listbox"
      aria-label="Commands and skills"
      className="absolute bottom-full left-0 right-0 z-50 mb-2 max-h-72 overflow-y-auto rounded-xl border border-line bg-panel p-1.5 shadow-shadow"
    >
      {menu.matches.map((item, i) => {
        const heading = i === 0 || menu.matches[i - 1].kind !== item.kind;
        return (
          <div key={`${item.kind}:${itemName(item)}`}>
            {heading ? (
              <div className="px-2.5 pb-1 pt-1 text-[0.7rem] font-medium uppercase tracking-wider text-muted">
                {item.kind === "command" ? "Commands" : "Skills"}
              </div>
            ) : null}
            <button
              type="button"
              role="option"
              aria-selected={i === menu.active}
              // mousedown, not click: the message box keeps its focus
              onMouseDown={(e) => {
                e.preventDefault();
                menu.pick(item);
              }}
              className={`flex w-full flex-col items-start gap-[0.1rem] rounded-lg px-2.5 py-1.5 text-left ${
                i === menu.active ? "bg-hover" : "hover:bg-hover"
              }`}
            >
              <span className="text-[0.85rem] text-text">
                <span className="font-semibold">{itemTitle(item)}</span>{" "}
                <span className="text-muted">/{itemName(item)}</span>
                <span className="ml-1.5 text-[0.68rem] text-muted">{tag(item)}</span>
              </span>
              <span className="line-clamp-1 text-[0.74rem] text-muted">
                {item.kind === "skill" ? item.skill.description : item.command.description}
              </span>
            </button>
          </div>
        );
      })}
    </div>
  );
}
