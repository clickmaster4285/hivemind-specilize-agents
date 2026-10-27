import type {
  AuthUser,
  JobEvent,
  Health,
  Job,
  ModelInfo,
  RunMode,
  ServerInfo,
  ServerLoginInput,
  ServerMode,
  Specialist,
  SpecialistInput,
  SpecialistOptions,
} from "./types";

const API_PORT = process.env.NEXT_PUBLIC_API_PORT || "8787";

function configuredOrigins(): string[] {
  const raw =
    process.env.NEXT_PUBLIC_API_ORIGINS ||
    process.env.NEXT_PUBLIC_API_ORIGIN ||
    `http://127.0.0.1:${API_PORT}`;
  return raw
    .split(",")
    .map((s) => s.trim().replace(/\/$/, ""))
    .filter(Boolean);
}

/** Resolve API base from env list using the current page hostname (LAN vs localhost). */
export function getApiOrigin(): string {
  const origins = configuredOrigins();
  if (typeof window !== "undefined") {
    const host = window.location.hostname;
    const match = origins.find((origin) => {
      try {
        return new URL(origin).hostname === host;
      } catch {
        return false;
      }
    });
    if (match) return match;
    if (host === "localhost" || host === "127.0.0.1" || /^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)) {
      const proto = window.location.protocol === "https:" ? "https:" : "http:";
      return `${proto}//${host}:${API_PORT}`;
    }
  }
  return origins[0] || `http://127.0.0.1:${API_PORT}`;
}

/** @deprecated use getApiOrigin() — kept for display fallbacks during SSR */
export const API_ORIGIN = configuredOrigins()[0] || `http://127.0.0.1:${API_PORT}`;

const TOKEN_KEY = "multeagent_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

/** Sent on window when the API refused the saved login (401): the session is over. */
export const SESSION_EXPIRED_EVENT = "multeagent:session-expired";

export function setToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

function apiUrl(path: string): string {
  return `${getApiOrigin()}${path.startsWith("/") ? path : `/${path}`}`;
}

function authHeaders(json = true): HeadersInit {
  const headers: Record<string, string> = {};
  if (json) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function readError(response: Response): Promise<string> {
  try {
    const data = await response.json();
    if (typeof data.detail === "string") return data.detail;
    return JSON.stringify(data.detail ?? data);
  } catch {
    return response.statusText || `HTTP ${response.status}`;
  }
}

function networkHint(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  if (/failed to fetch|networkerror|network error|load failed/i.test(msg)) {
    return `Cannot reach API at ${getApiOrigin()}. Start: python main.py --gui (${msg})`;
  }
  return msg;
}

async function request<T>(path: string, init?: RequestInit, opts?: { timeoutMs?: number }): Promise<T> {
  // A slow request (e.g. an SSH test login to an unreachable server) is aborted with a clear message
  // instead of hanging until the browser gives up and reports a bare "Failed to fetch".
  const controller = opts?.timeoutMs ? new AbortController() : null;
  const timer = controller ? window.setTimeout(() => controller.abort(), opts!.timeoutMs) : null;
  try {
    const response = await fetch(apiUrl(path), {
      ...init,
      signal: controller?.signal,
      headers: { ...authHeaders(!(init?.body instanceof FormData)), ...(init?.headers || {}) },
    });
    if (!response.ok) {
      // Drop stale session so the login screen returns instead of a half-authed UI.
      if (response.status === 401 && !path.includes("/api/auth/login")) {
        setToken(null);
        window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT)); // useAuth shows the login again
      }
      throw new Error(await readError(response));
    }
    if (response.status === 204) return undefined as T;
    return response.json();
  } catch (err) {
    if (controller?.signal.aborted) {
      throw new Error("the server did not answer in time — check the address, that port is open, and the login");
    }
    throw new Error(networkHint(err));
  } finally {
    if (timer) window.clearTimeout(timer);
  }
}

export async function login(username: string, password: string) {
  const data = await request<{
    access_token: string;
    user: AuthUser;
  }>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  setToken(data.access_token);
  return data.user;
}

export async function me(): Promise<AuthUser> {
  return request("/api/auth/me");
}

export async function getHealth(): Promise<Health> {
  return request("/api/health");
}

export async function listSpecialists(): Promise<Specialist[]> {
  return request("/api/specialists");
}

export async function specialistOptions(): Promise<SpecialistOptions> {
  return request("/api/specialists/options");
}

export async function createSpecialist(payload: SpecialistInput): Promise<Specialist> {
  return request("/api/specialists", { method: "POST", body: JSON.stringify(payload) });
}

export async function updateSpecialist(id: string, payload: Partial<SpecialistInput>): Promise<Specialist> {
  return request(`/api/specialists/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function deleteSpecialist(id: string): Promise<void> {
  await request(`/api/specialists/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function resetSpecialists(): Promise<Specialist[]> {
  return request("/api/specialists/reset", { method: "POST", body: "{}" });
}

/** Server logins live in the API's memory for this session only (admins). */
export async function listServers(): Promise<ServerInfo[]> {
  return request("/api/servers");
}

export async function addServer(payload: ServerLoginInput): Promise<ServerInfo> {
  // the API logs in over SSH to test the credentials; bound the wait so a dead host fails clearly
  return request("/api/servers", { method: "POST", body: JSON.stringify(payload) }, { timeoutMs: 30000 });
}

export async function removeServer(id: string): Promise<void> {
  await request(`/api/servers/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function setServerMode(id: string, mode: ServerMode): Promise<ServerInfo> {
  return request(`/api/servers/${encodeURIComponent(id)}/mode`, {
    method: "PUT",
    body: JSON.stringify({ mode }),
  });
}

/** Logout: the API forgets every server login of this user. */
export async function forgetServers(): Promise<void> {
  await request("/api/servers", { method: "DELETE" });
}

/** The user clicked Run / Cancel on a command the agent asked to run (operate_server). */
export async function confirmCommand(jobId: string, commandId: string, approved: boolean): Promise<void> {
  await request(`/api/jobs/${encodeURIComponent(jobId)}/confirm`, {
    method: "POST",
    body: JSON.stringify({ command_id: commandId, approved }),
  });
}

export async function listModels(refresh = false): Promise<ModelInfo[]> {
  return request(`/api/models${refresh ? "?refresh=true" : ""}`);
}

export type ImageForceIntent =
  | "describe"
  | "generate"
  | "edit"
  | "inpaint"
  | "doc_gen"
  | "web_search";
export type ImageProfile = "fast" | "quality" | "balanced";

export async function createJob(payload: {
  query: string;
  mode: RunMode;
  /** "auto" = the assistant picks · "none" = no specialist · or a specialist id */
  specialist?: string;
  model?: string;
  allow_overflow?: boolean;
  images?: { filename: string; mime: string; data: string }[];
  /** Prior turns so follow-ups like “write that as docx” keep context */
  conversation?: { role: "user" | "assistant"; content: string }[];
  force_intent?: ImageForceIntent;
  image_profile?: ImageProfile;
  image_strength?: number;
  /** The chat this job belongs to, so history can regroup the conversation after a reload */
  thread_id?: string;
  /** "deep" = the Deep research toggle (research the topic across many sources) */
  depth?: string;
  /** Options toggle: offer a few candidate approaches to pick from before the full answer */
  options?: boolean;
  /** A follow-up answer (picked an approach card / answered a clarify form): routes normally but won't re-clarify */
  followup?: boolean;
}): Promise<Job> {
  // the user's own time zone (e.g. "Asia/Karachi"): answers tell their local time, no location permission
  let client_time_zone: string | undefined;
  try {
    client_time_zone = Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    client_time_zone = undefined;
  }
  return request("/api/jobs", { method: "POST", body: JSON.stringify({ ...payload, client_time_zone }) });
}

export async function listJobs(limit = 100): Promise<Job[]> {
  return request(`/api/jobs?limit=${Math.min(Math.max(limit, 1), 100)}`);
}

export async function cancelJob(jobId: string): Promise<Job> {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
    body: "{}",
  });
}

/** Soft-delete jobs (conversation turns). Rows are kept with deleted_at set. */
export async function softDeleteJobs(jobIds: string[]): Promise<{
  status: string;
  deleted_ids: string[];
  count: number;
}> {
  return request("/api/jobs/soft-delete", {
    method: "POST",
    body: JSON.stringify({ job_ids: jobIds }),
  });
}

// ── Chat history: thread + project metadata (per user, server-side, so it syncs across devices).
export type ThreadMeta = {
  id: string;
  title: string;
  projectId: string | null;
  pinned: boolean;
  deletedAt: string | null;
  updatedAt: string | null;
};
export type ProjectMeta = { id: string; name: string; createdAt: string | null };

type ThreadRow = { id: string; title: string; project_id?: string | null; pinned?: boolean; deleted_at?: string | null; updated_at?: string | null };
type ProjectRow = { id: string; name: string; created_at?: string | null };

function toThreadMeta(t: ThreadRow): ThreadMeta {
  return {
    id: t.id,
    title: t.title,
    projectId: t.project_id ?? null,
    pinned: Boolean(t.pinned),
    deletedAt: t.deleted_at ?? null,
    updatedAt: t.updated_at ?? null,
  };
}
function toProjectMeta(p: ProjectRow): ProjectMeta {
  return { id: p.id, name: p.name, createdAt: p.created_at ?? null };
}

export async function fetchHistory(): Promise<{ threads: ThreadMeta[]; projects: ProjectMeta[] }> {
  const raw = await request<{ threads: ThreadRow[]; projects: ProjectRow[] }>("/api/history");
  return { threads: (raw.threads || []).map(toThreadMeta), projects: (raw.projects || []).map(toProjectMeta) };
}

/** Rename / pin / move a chat. Only the given fields change; `project_id: null` removes it from a project. */
export async function putThread(
  id: string,
  changes: { title?: string; pinned?: boolean; project_id?: string | null },
): Promise<ThreadMeta> {
  return toThreadMeta(await request(`/api/threads/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(changes) }));
}

export async function deleteThreadApi(id: string): Promise<void> {
  await request(`/api/threads/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** One-time migration of browser-stored history; the server ignores it once the user has any thread. */
export async function importHistory(
  threads: unknown[],
  projects: unknown[],
): Promise<{ imported: boolean }> {
  return request("/api/history/import", { method: "POST", body: JSON.stringify({ threads, projects }) });
}

export async function createProjectApi(name: string): Promise<ProjectMeta> {
  return toProjectMeta(await request("/api/projects", { method: "POST", body: JSON.stringify({ name }) }));
}

export async function renameProjectApi(id: string, name: string): Promise<ProjectMeta> {
  return toProjectMeta(await request(`/api/projects/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify({ name }) }));
}

export async function deleteProjectApi(id: string): Promise<void> {
  await request(`/api/projects/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function fetchJobAttachment(jobId: string, filename: string): Promise<string> {
  const response = await fetch(
    apiUrl(`/api/jobs/${encodeURIComponent(jobId)}/attachments/${encodeURIComponent(filename)}`),
    { headers: authHeaders(false) },
  );
  if (!response.ok) throw new Error(await readError(response));
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

export async function getJobHistory(jobId: string): Promise<{
  job: Job;
  events: JobEvent[];
}> {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/history`);
}

export async function streamJobEvents(
  jobId: string,
  onEvent: (event: JobEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(apiUrl(`/api/jobs/${encodeURIComponent(jobId)}/events`), {
      headers: authHeaders(false),
      signal,
    });
  } catch (err) {
    throw new Error(networkHint(err));
  }
  if (!response.ok) throw new Error(await readError(response));
  if (!response.body) throw new Error("Empty event stream");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        const data = chunk
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trim())
          .join("");
        if (!data) continue;
        onEvent(JSON.parse(data) as JobEvent);
      }
    }
  } catch (err) {
    throw new Error(networkHint(err));
  }
}
