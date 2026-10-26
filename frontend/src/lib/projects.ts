/** Projects group chats (via each chat's projectId). The metadata lives on the server (see api.ts
 *  createProjectApi/renameProjectApi/deleteProjectApi), so it syncs across devices; only the "which
 *  project is selected" pointer stays in this browser. */

export type Project = { id: string; name: string; createdAt: string | null };

const ACTIVE_KEY = "multeagent_active_project";
const LEGACY_KEY = "multeagent_projects_v1"; // pre-server projects, migrated once on first login

export function activeProjectId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACTIVE_KEY);
}

export function setActiveProjectId(id: string | null) {
  if (typeof window === "undefined") return;
  if (id) localStorage.setItem(ACTIVE_KEY, id);
  else localStorage.removeItem(ACTIVE_KEY);
}

/** Old browser-stored projects, read once so first login can migrate them to the server. */
export function readLegacyProjects(): unknown[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(LEGACY_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}
