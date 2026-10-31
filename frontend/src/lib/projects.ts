/** Projects group chats (via each chat's projectId). The metadata lives on the server (see api.ts
 *  createProjectApi/renameProjectApi/deleteProjectApi), so it syncs across devices; only the "which
 *  project is selected" pointer stays in this browser. */

export type Project = { id: string; name: string; createdAt: string | null };

const ACTIVE_KEY = "multeagent_active_project";
const LEGACY_KEY = "multeagent_projects_v1"; // pre-server projects, migrated once on first login

// Per-device AND per-user: shared browsers must not leak the selected project between accounts.
function activeKey(userId: string | number): string {
  return `${ACTIVE_KEY}:${userId}`;
}

export function activeProjectId(userId: string | number): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(activeKey(userId));
}

export function setActiveProjectId(userId: string | number, id: string | null) {
  if (typeof window === "undefined") return;
  if (id) localStorage.setItem(activeKey(userId), id);
  else localStorage.removeItem(activeKey(userId));
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

/** Drop the pre-server blob once it has been migrated, so a second user on this browser cannot adopt it. */
export function clearLegacyProjects() {
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(LEGACY_KEY);
  } catch {
    // ignore — storage may be unavailable; the per-user migrated flag still prevents re-import
  }
}
