import { useCallback, useMemo, useState } from "react";

import {
  createProjectApi,
  deleteProjectApi,
  deleteThreadApi,
  fetchHistory,
  importHistory,
  listJobs,
  putThread,
  softDeleteJobs,
  type ThreadMeta,
} from "@/lib/api";
import {
  activeProjectId,
  clearLegacyProjects,
  readLegacyProjects,
  setActiveProjectId,
  type Project,
} from "@/lib/projects";
import {
  buildThreads,
  clearLegacyThreads,
  getActiveThreadId,
  readLegacyThreads,
  setActiveThreadId,
  type ChatThread,
} from "@/lib/threads";
import type { Job } from "@/lib/types";

// set once the browser's old localStorage history has been pushed to the server
const MIGRATED_KEY = "multeagent_history_migrated";

/** Chat history: jobs from the API, plus chat/project metadata persisted per user on the server (so it
 *  syncs across devices). Threads are derived by grouping jobs by thread_id and layering that metadata. */
export function useHistory() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [threadMeta, setThreadMeta] = useState<ThreadMeta[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [activeThreadId, setActiveThreadIdState] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  // The signed-in user. All per-device pointers (open chat/project) are namespaced by it so a shared
  // browser never shows one person another's chats; null until loadHistory() runs after sign-in.
  const [userId, setUserId] = useState<number | null>(null);

  const jobById = useMemo(() => new Map(jobs.map((j) => [j.id, j])), [jobs]);
  const threads = useMemo(() => buildThreads(jobs, threadMeta), [jobs, threadMeta]);

  const filteredThreads = useMemo(() => {
    let list = threads;
    if (projectId) list = list.filter((t) => t.projectId === projectId);
    const q = filter.trim().toLowerCase();
    if (q) {
      list = list.filter((t) => {
        if (t.title.toLowerCase().includes(q)) return true;
        return t.jobIds.some((id) => {
          const job = jobById.get(id);
          return job ? job.query.toLowerCase().includes(q) || job.status.includes(q) : false;
        });
      });
    }
    return list;
  }, [threads, projectId, filter, jobById]);

  const activeProject = projects.find((p) => p.id === projectId) || null;

  const refreshJobs = useCallback(async () => {
    const data = await listJobs(100);
    setJobs(data);
    return data;
  }, []);

  // Put a just-created job into state at once (before refreshJobs lists it) so its chat keeps it even if
  // the user switches away and comes back before the answer finishes.
  const addJob = useCallback((job: Job) => {
    setJobs((prev) => (prev.some((j) => j.id === job.id) ? prev : [job, ...prev]));
  }, []);

  // Drop jobs from state (an inline edit replaced a message and the turns after it).
  const dropJobs = useCallback((ids: string[]) => {
    const set = new Set(ids);
    setJobs((prev) => prev.filter((j) => !set.has(j.id)));
  }, []);

  /** Load the signed-in user's chat/project metadata from the server; on their first run, migrate any old
   *  browser-stored history into THIS account, then delete the local blob so no other account can adopt it.
   *  The migrated flag and the open-chat/project pointers are namespaced per user. */
  const loadHistory = useCallback(async (uid: number) => {
    setUserId(uid);
    let hist = await fetchHistory();
    const migratedKey = `${MIGRATED_KEY}:${uid}`;
    const migrated = typeof window !== "undefined" && localStorage.getItem(migratedKey);
    if (!hist.threads.length && !migrated) {
      const legacyThreads = readLegacyThreads();
      const legacyProjects = readLegacyProjects();
      if (legacyThreads.length || legacyProjects.length) {
        try {
          await importHistory(legacyThreads, legacyProjects);
          hist = await fetchHistory();
        } catch {
          // best-effort: chats still rebuild from jobs' thread_id even if the import fails
        }
        // consume the blob so a second user on this shared browser can never import it as their own
        clearLegacyThreads();
        clearLegacyProjects();
      }
      if (typeof window !== "undefined") localStorage.setItem(migratedKey, "1");
    }
    setThreadMeta(hist.threads);
    setProjects(hist.projects);
    // restore this user's own open chat/project now that we know who they are
    setActiveThreadIdState(getActiveThreadId(uid));
    setProjectId(activeProjectId(uid));
  }, []);

  const selectThread = useCallback((id: string | null) => {
    setActiveThreadIdState(id);
    if (userId !== null) setActiveThreadId(userId, id);
  }, [userId]);

  const selectProject = useCallback((id: string | null) => {
    setProjectId(id);
    if (userId !== null) setActiveProjectId(userId, id);
  }, [userId]);

  /** Show a brand-new chat at once, before its server row exists (created when the first job is sent). */
  const noteThread = useCallback((id: string, title: string, forProjectId: string | null = null) => {
    setThreadMeta((prev) => {
      const now = new Date().toISOString();
      if (prev.some((m) => m.id === id)) return prev.map((m) => (m.id === id ? { ...m, updatedAt: now } : m));
      return [...prev, { id, title, projectId: forProjectId, pinned: false, deletedAt: null, updatedAt: now }];
    });
  }, []);

  const patchMeta = useCallback((id: string, patch: Partial<ThreadMeta>) => {
    setThreadMeta((prev) => {
      const now = new Date().toISOString();
      if (prev.some((m) => m.id === id)) return prev.map((m) => (m.id === id ? { ...m, ...patch, updatedAt: now } : m));
      return [
        { id, title: "New chat", projectId: null, pinned: false, deletedAt: null, updatedAt: now, ...patch },
        ...prev,
      ];
    });
  }, []);

  const togglePin = useCallback(
    (thread: ChatThread) => {
      const pinned = !thread.pinned;
      patchMeta(thread.id, { pinned });
      void putThread(thread.id, { pinned }).catch(() => undefined);
    },
    [patchMeta],
  );

  const rename = useCallback(
    (thread: ChatThread, title: string) => {
      const next = title.trim();
      if (!next) return;
      patchMeta(thread.id, { title: next });
      void putThread(thread.id, { title: next }).catch(() => undefined);
    },
    [patchMeta],
  );

  const moveToProject = useCallback(
    (thread: ChatThread | string, pid: string | null) => {
      const id = typeof thread === "string" ? thread : thread.id;
      patchMeta(id, { projectId: pid });
      void putThread(id, { project_id: pid }).catch(() => undefined);
    },
    [patchMeta],
  );

  /** Asks for a name; returns the new project, or null when cancelled. */
  const newProject = useCallback(async (): Promise<Project | null> => {
    const name = prompt("Project name");
    if (!name?.trim()) return null;
    const project = await createProjectApi(name.trim());
    setProjects((prev) => [project, ...prev]);
    selectProject(project.id);
    return project;
  }, [selectProject]);

  const removeProject = useCallback(
    async (id: string) => {
      setProjects((prev) => prev.filter((p) => p.id !== id));
      setThreadMeta((prev) => prev.map((m) => (m.projectId === id ? { ...m, projectId: null } : m)));
      if (projectId === id) selectProject(null);
      await deleteProjectApi(id).catch(() => undefined);
    },
    [projectId, selectProject],
  );

  /** Hide the chat and its jobs; returns true when it was the open chat. */
  const removeThread = useCallback(
    async (thread: ChatThread) => {
      if (thread.jobIds.length > 0) await softDeleteJobs(thread.jobIds);
      await deleteThreadApi(thread.id).catch(() => undefined);
      setThreadMeta((prev) => prev.filter((m) => m.id !== thread.id));
      setJobs((prev) => prev.filter((j) => !thread.jobIds.includes(j.id)));
      const wasOpen = activeThreadId === thread.id;
      if (wasOpen) selectThread(null);
      return wasOpen;
    },
    [activeThreadId, selectThread],
  );

  // Sign-out: wipe this account's data from memory and forget who was signed in. The per-user pointers
  // stay in localStorage (untouched here), so the same person returns to their last open chat; a different
  // person signing in reads only their own namespaced pointers.
  const clear = useCallback(() => {
    setJobs([]);
    setThreadMeta([]);
    setProjects([]);
    setActiveThreadIdState(null);
    setProjectId(null);
    setUserId(null);
  }, []);

  return {
    jobs,
    jobById,
    threads,
    filteredThreads,
    projects,
    projectId,
    activeProject,
    activeThreadId,
    filter,
    setFilter,
    refreshJobs,
    addJob,
    dropJobs,
    loadHistory,
    noteThread,
    selectThread,
    selectProject,
    newProject,
    removeProject,
    togglePin,
    rename,
    moveToProject,
    removeThread,
    clear,
  };
}

export type HistoryApi = ReturnType<typeof useHistory>;
