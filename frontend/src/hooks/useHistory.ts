import { useCallback, useEffect, useMemo, useState } from "react";

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
import { activeProjectId, readLegacyProjects, setActiveProjectId, type Project } from "@/lib/projects";
import {
  buildThreads,
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

  // restore the per-device pointers (which chat/project was open) after mount
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time load from browser storage
    setProjectId(activeProjectId());
    setActiveThreadIdState(getActiveThreadId());
  }, []);

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

  /** Load chat/project metadata from the server; on first run, migrate any old browser-stored history. */
  const loadHistory = useCallback(async () => {
    let hist = await fetchHistory();
    const migrated = typeof window !== "undefined" && localStorage.getItem(MIGRATED_KEY);
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
      }
      if (typeof window !== "undefined") localStorage.setItem(MIGRATED_KEY, "1");
    }
    setThreadMeta(hist.threads);
    setProjects(hist.projects);
  }, []);

  const selectThread = useCallback((id: string | null) => {
    setActiveThreadIdState(id);
    setActiveThreadId(id);
  }, []);

  const selectProject = useCallback((id: string | null) => {
    setProjectId(id);
    setActiveProjectId(id);
  }, []);

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

  const clear = useCallback(() => {
    setJobs([]);
    setThreadMeta([]);
    setProjects([]);
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
