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

/** Pinned chats first, then by age; within a group the list keeps its order (newest first). */
export function groupThreads(threads: ChatThread[], now: Date = new Date()): ThreadGroup[] {
  const groups: ThreadGroup[] = [];
  const add = (label: string, thread: ChatThread) => {
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.threads.push(thread);
    else groups.push({ label, threads: [thread] });
  };
  for (const t of threads.filter((t) => t.pinned)) add("Pinned", t);
  for (const t of threads.filter((t) => !t.pinned)) add(groupLabel(new Date(t.updatedAt), now), t);
  return groups;
}

/** "Nov 17, 2026, 09:41" — the hover card's date. */
export function threadDate(thread: ChatThread): string {
  return new Date(thread.updatedAt).toLocaleString(undefined, {
    month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}
