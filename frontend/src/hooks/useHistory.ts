import { useCallback, useEffect, useMemo, useState } from "react";

import { listJobs, softDeleteJobs } from "@/lib/api";
import {
  activeProjectId,
  createProject,
  deleteProject,
  listProjects,
  setActiveProjectId,
  type Project,
} from "@/lib/projects";
import {
  ensureThreadsForJobs,
  getActiveThreadId,
  listThreads,
  pinThread,
  renameThread,
  setThreadProject,
  setActiveThreadId,
  softDeleteThread,
  type ChatThread,
} from "@/lib/threads";
import type { Job } from "@/lib/types";

/** Chat history: jobs from the API, chat threads and projects (kept in the browser), search filter. */
export function useHistory() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [activeThreadId, setActiveThreadIdState] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  // Saved chats live in localStorage, which the server render cannot read: load them after mount
  // (reading during render would make the server HTML and the first client render differ).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time load from browser storage
    setProjects(listProjects());
    setProjectId(activeProjectId());
    setThreads(listThreads());
    setActiveThreadIdState(getActiveThreadId());
  }, []);

  const jobById = useMemo(() => new Map(jobs.map((j) => [j.id, j])), [jobs]);

  const filteredThreads = useMemo(() => {
    let list = threads;
    if (projectId) {
      const project = projects.find((p) => p.id === projectId);
      const projectJobs = new Set(project?.jobIds || []);
      list = list.filter((t) => t.projectId === projectId || t.jobIds.some((id) => projectJobs.has(id)));
    }
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
  }, [threads, projectId, projects, filter, jobById]);

  const activeProject = projects.find((p) => p.id === projectId) || null;

  const refreshJobs = useCallback(async () => {
    const data = await listJobs(100);
    setJobs(data);
    setThreads(ensureThreadsForJobs(data));
    return data;
  }, []);

  const selectThread = useCallback((id: string | null) => {
    setActiveThreadIdState(id);
    setActiveThreadId(id);
  }, []);

  const selectProject = useCallback((id: string | null) => {
    setProjectId(id);
    setActiveProjectId(id);
  }, []);

  /** Asks for a name; returns the new project, or null when cancelled. */
  const newProject = useCallback((): Project | null => {
    const name = prompt("Project name");
    if (!name?.trim()) return null;
    const project = createProject(name);
    setProjects(listProjects());
    setProjectId(project.id);
    setActiveProjectId(project.id);
    return project;
  }, []);

  const removeProject = useCallback(
    (id: string) => {
      setProjects(deleteProject(id));
      if (projectId === id) selectProject(null);
    },
    [projectId, selectProject],
  );

  const togglePin = useCallback((thread: ChatThread) => setThreads(pinThread(thread.id)), []);
  const rename = useCallback((thread: ChatThread, title: string) => setThreads(renameThread(thread.id, title)), []);
  const moveToProject = useCallback(
    (thread: ChatThread, pid: string | null) => setThreads(setThreadProject(thread.id, pid)),
    [],
  );

  /** Hide the chat and its jobs; returns true when it was the open chat. */
  const removeThread = useCallback(
    async (thread: ChatThread) => {
      if (thread.jobIds.length > 0) await softDeleteJobs(thread.jobIds);
      setThreads(softDeleteThread(thread.id));
      setJobs((prev) => prev.filter((j) => !thread.jobIds.includes(j.id)));
      const wasOpen = activeThreadId === thread.id;
      if (wasOpen) selectThread(null);
      return wasOpen;
    },
    [activeThreadId, selectThread],
  );

  const clear = useCallback(() => setJobs([]), []);

  return {
    jobs,
    jobById,
    threads,
    setThreads,
    filteredThreads,
    projects,
    setProjects,
    projectId,
    activeProject,
    activeThreadId,
    filter,
    setFilter,
    refreshJobs,
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
