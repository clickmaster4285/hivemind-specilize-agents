/** Chat history grouped by when each chat was last used, like DeepSeek's "7 Days / 30 Days / 2026-10" (BACKLOG L24 U3).
 *  Pure: no React. */
import type { ChatThread } from "@/lib/threads";

const DAY_MS = 24 * 60 * 60 * 1000;
const WEEK_DAYS = 7;                 // "Previous 7 days"
const MONTH_DAYS = 30;               // "Previous 30 days"; older chats group by calendar month

export type ThreadGroup = { label: string; threads: ChatThread[] };

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

function groupLabel(updated: Date, now: Date): string {
  const days = Math.floor((startOfDay(now) - startOfDay(updated)) / DAY_MS);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < WEEK_DAYS) return "Previous 7 days";
  if (days < MONTH_DAYS) return "Previous 30 days";
  return updated.toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

/** The sidebar's filter menu: group by date or not, sort by last activity or title, pinned chats only. */
export type ThreadView = { group: "date" | "none"; sort: "recent" | "title"; pinnedOnly: boolean };
export const DEFAULT_VIEW: ThreadView = { group: "date", sort: "recent", pinnedOnly: false };

/** Pinned chats first, then by age (or one "All chats" group); within a group the chosen order. */
export function groupThreads(threads: ChatThread[], view: ThreadView = DEFAULT_VIEW, now: Date = new Date()): ThreadGroup[] {
  const shown = view.pinnedOnly ? threads.filter((t) => t.pinned) : threads;
  const ordered = view.sort === "title"
    ? [...shown].sort((a, b) => a.title.localeCompare(b.title))
    : [...shown].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  const groups: ThreadGroup[] = [];
  const add = (label: string, thread: ChatThread) => {
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.threads.push(thread);
    else groups.push({ label, threads: [thread] });
  };
  for (const t of ordered.filter((t) => t.pinned)) add("Pinned", t);
  for (const t of ordered.filter((t) => !t.pinned)) {
    add(view.group === "date" && view.sort === "recent" ? groupLabel(new Date(t.updatedAt), now) : "All chats", t);
  }
  return groups;
}

/** "Nov 17, 2026, 09:41" — the hover card's date. */
export function threadDate(thread: ChatThread): string {
  return new Date(thread.updatedAt).toLocaleString(undefined, {
    month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}
