/** Chat threads: one chat holds many jobs. The metadata (title, pin, project, deletion) lives on the
 *  server (see api.ts fetchHistory/putThread/…), so it syncs across devices; a chat's messages are its
 *  jobs, grouped by each job's thread_id. Only the "which chat is open" pointer stays in this browser. */

import type { ThreadMeta } from "@/lib/api";
import type { Job } from "@/lib/types";

export type ChatThread = {
  id: string;
  title: string;
  jobIds: string[];
  projectId: string | null;
  pinned: boolean;
  /** ISO timestamp when soft-deleted; null = active */
  deletedAt: string | null;
  updatedAt: string;
};

const ACTIVE_KEY = "multeagent_active_thread";
const LEGACY_KEY = "multeagent_threads_v1"; // pre-server history, migrated once on first login

export function getActiveThreadId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACTIVE_KEY);
}

export function setActiveThreadId(id: string | null) {
  if (typeof window === "undefined") return;
  if (id) localStorage.setItem(ACTIVE_KEY, id);
  else localStorage.removeItem(ACTIVE_KEY);
}

function sortThreads(threads: ChatThread[]): ChatThread[] {
  return [...threads].sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
    return b.updatedAt.localeCompare(a.updatedAt);
  });
}

/** Build the chat list the sidebar shows: group jobs by their thread_id, then layer on the server
 *  metadata (title, pin, project, deletion). A legacy job with no thread_id becomes its own chat;
 *  a chat that has a metadata row but no loaded jobs yet (a brand-new empty chat) is kept too. */
export function buildThreads(jobs: Job[], metas: ThreadMeta[]): ChatThread[] {
  const metaById = new Map(metas.map((m) => [m.id, m]));
  const byId = new Map<string, ChatThread>();
  const now = new Date().toISOString();
  const ordered = [...jobs].sort((a, b) => (a.created_at || "").localeCompare(b.created_at || ""));
  for (const job of ordered) {
    if (job.deleted_at) continue;
    const tid = job.thread_id || `job-${job.id}`;
    let thread = byId.get(tid);
    if (!thread) {
      const m = metaById.get(tid);
      thread = {
        id: tid,
        title: m?.title || job.query.trim().slice(0, 42) || "Chat",
        jobIds: [],
        projectId: m?.projectId ?? null,
        pinned: m?.pinned ?? false,
        deletedAt: m?.deletedAt ?? null,
        updatedAt: m?.updatedAt || job.created_at || now,
      };
      byId.set(tid, thread);
    }
    thread.jobIds.push(job.id);
  }
  for (const m of metas) {
    if (byId.has(m.id)) continue;
    byId.set(m.id, {
      id: m.id,
      title: m.title,
      jobIds: [],
      projectId: m.projectId,
      pinned: m.pinned,
      deletedAt: m.deletedAt,
      updatedAt: m.updatedAt || now,
    });
  }
  return sortThreads([...byId.values()].filter((t) => !t.deletedAt));
}

/** Old browser-stored threads, read once so first login can migrate them to the server. */
export function readLegacyThreads(): unknown[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(LEGACY_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}
