import type {
  AuthUser,
  CatalogSkill,
  JobEvent,
  Health,
  Job,
  ManagedUser,
  ModuleInfo,
  ModelInfo,
  RunMode,
  ServerInfo,
  ServerLoginInput,
  ServerMode,
  Skill,
  SkillCategory,
  SkillInput,
  Specialist,
  SpecialistInput,
  SpecialistOptions,
  UsageSummary,
  UserCreateInput,
  UserUpdateInput,
} from "./types";

/** Sent on window when the session is over (a 401 the refresh could not fix): useAuth shows the login. */
export const SESSION_EXPIRED_EVENT = "multeagent:session-expired";

// auth calls that answer for themselves: a 401 from them is the answer, not a reason to refresh
const NO_REFRESH = new Set(["/api/auth/login", "/api/auth/refresh", "/api/auth/logout"]);

let refreshing: Promise<boolean> | null = null;

/** Trade the refresh cookie for new session cookies — one request however many calls got a 401 together. */
function refreshSession(): Promise<boolean> {
  refreshing ??= fetch("/api/auth/refresh", { method: "POST" })
    .then((response) => response.ok)
    .catch(() => false)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

/**
 * fetch on the API. Calls go to the page's own origin (the Next.js rewrite or the HTTPS proxy passes /api
 * on), so the HttpOnly session cookies go with them and page scripts never see a token. A 401 refreshes the
 * session once and repeats the call; if it is still refused, the session is over.
 */
async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  if (typeof init?.body === "string" && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const send = () => fetch(path, { ...init, headers });
  let response = await send();
  if (response.status !== 401 || NO_REFRESH.has(path)) return response;
  if (await refreshSession()) response = await send();
  if (response.status === 401) window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
  return response;
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
    return `Cannot reach the API. Start: python main.py --gui (${msg})`;
  }
  return msg;
}

async function request<T>(path: string, init?: RequestInit, opts?: { timeoutMs?: number }): Promise<T> {
  // A slow request (e.g. an SSH test login to an unreachable server) is aborted with a clear message
  // instead of hanging until the browser gives up and reports a bare "Failed to fetch".
  const controller = opts?.timeoutMs ? new AbortController() : null;
  const timer = controller ? window.setTimeout(() => controller.abort(), opts!.timeoutMs) : null;
  try {
    const response = await apiFetch(path, { ...init, signal: controller?.signal });
    if (!response.ok) throw new Error(await readError(response));
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
  const data = await request<{ user: AuthUser }>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  return data.user;
}

/** Sign out: the server revokes the session and clears its cookies. */
export async function logout(): Promise<void> {
  await request("/api/auth/logout", { method: "POST" });
}

export async function me(): Promise<AuthUser> {
  return request("/api/auth/me");
}

/** Accounts (admin only). Self-serve registration stays disabled — admins create users here. */
export async function listUsers(): Promise<ManagedUser[]> {
  return request("/api/auth/users");
}

/** The modules an admin can give a member (Devices, Skills, Usage…). */
export async function listModules(): Promise<ModuleInfo[]> {
  return request("/api/auth/users/modules");
}

export async function createUser(payload: UserCreateInput): Promise<ManagedUser> {
  return request("/api/auth/users", { method: "POST", body: JSON.stringify(payload) });
}

export async function updateUser(id: number, payload: UserUpdateInput): Promise<ManagedUser> {
  return request(`/api/auth/users/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
}

/** "Delete" = deactivate: the account can no longer sign in, but its chats/queries are kept. */
export async function deactivateUser(id: number): Promise<ManagedUser> {
  return request(`/api/auth/users/${id}`, { method: "DELETE" });
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

/** Yours (team + personal) and what /name can run (usable: one per name, built-in skills included). */
export async function listSkills(): Promise<{ skills: Skill[]; usable: Skill[]; can_manage_team: boolean }> {
  return request("/api/skills");
}

/** Discover: the built-in skills (added = already in Yours) and their categories with counts. */
export async function listSkillCatalog(): Promise<{ skills: CatalogSkill[]; categories: SkillCategory[] }> {
  return request("/api/skills/catalog");
}

/** A new skill in Yours: for the team (admins) or only for me. */
export async function createSkill(skill: SkillInput, team: boolean): Promise<Skill> {
  return request("/api/skills", { method: "POST", body: JSON.stringify({ ...skill, team }) });
}

export async function updateSkill(id: number, changes: Partial<SkillInput>): Promise<Skill> {
  return request(`/api/skills/${id}`, { method: "PUT", body: JSON.stringify(changes) });
}

export async function deleteSkill(id: number): Promise<void> {
  await request(`/api/skills/${id}`, { method: "DELETE" });
}

/** A SKILL.md file's text (written for MulteAgent or another assistant). */
export async function uploadSkill(text: string, team: boolean): Promise<Skill> {
  return request("/api/skills/upload", { method: "POST", body: JSON.stringify({ text, team }) });
}

/** Discover's "+": copy a built-in skill into Yours. */
export async function addCatalogSkill(name: string, team: boolean): Promise<Skill> {
  return request(`/api/skills/catalog/${encodeURIComponent(name)}/add`, { method: "POST", body: JSON.stringify({ team }) });
}

/** The skill as a SKILL.md file → object URL + file name. */
export async function downloadSkill(id: number): Promise<{ url: string; filename: string }> {
  const response = await apiFetch(`/api/skills/${id}/download`);
  if (!response.ok) throw new Error(await readError(response));
  return { url: URL.createObjectURL(await response.blob()), filename: attachmentName(response, "SKILL.md") };
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
  /** "Try again, better": the user's reason for a 👎, steered into this regenerate (feedback phase 2) */
  refine?: string;
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
  /** "Summarize & continue in a new chat": the chat this one continues, and the summary it carried over. */
  continuedFrom?: string | null;
  carriedSummary?: string | null;
};
export type ProjectMeta = { id: string; name: string; createdAt: string | null };

type ThreadRow = {
  id: string;
  title: string;
  project_id?: string | null;
  pinned?: boolean;
  deleted_at?: string | null;
  updated_at?: string | null;
  continued_from?: string | null;
  carried_summary?: string | null;
};
type ProjectRow = { id: string; name: string; created_at?: string | null };

function toThreadMeta(t: ThreadRow): ThreadMeta {
  return {
    id: t.id,
    title: t.title,
    projectId: t.project_id ?? null,
    pinned: Boolean(t.pinned),
    deletedAt: t.deleted_at ?? null,
    updatedAt: t.updated_at ?? null,
    continuedFrom: t.continued_from ?? null,
    carriedSummary: t.carried_summary ?? null,
  };
}

/** "Save summary": the chat summarised by the model (goal, key facts and decisions, open questions). */
export async function summarizeThread(threadId: string): Promise<string> {
  const data = await request<{ summary: string }>(`/api/threads/${encodeURIComponent(threadId)}/summary`, {
    method: "POST",
  });
  return data.summary;
}

/** "Summarize & continue in a new chat": the server summarises this chat and starts `newThreadId` from it. */
export async function continueThread(threadId: string, newThreadId: string): Promise<{ thread: ThreadMeta; summary: string }> {
  const data = await request<{ thread: ThreadRow; summary: string }>(
    `/api/threads/${encodeURIComponent(threadId)}/continue`,
    { method: "POST", body: JSON.stringify({ new_thread_id: newThreadId }) },
  );
  return { thread: toThreadMeta(data.thread), summary: data.summary };
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

/** Record the user's 👍/👎 (and optional note) on one answer. */
export async function sendFeedback(jobId: string, rating: "up" | "down", note?: string): Promise<void> {
  await request("/api/feedback", { method: "POST", body: JSON.stringify({ job_id: jobId, rating, note }) });
}

export async function fetchJobAttachment(jobId: string, filename: string): Promise<string> {
  const response = await apiFetch(`/api/jobs/${encodeURIComponent(jobId)}/attachments/${encodeURIComponent(filename)}`);
  if (!response.ok) throw new Error(await readError(response));
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

/** Token usage over the last `days`: everyone's for an admin, your own otherwise (BACKLOG L11). */
export async function getUsage(days: number): Promise<UsageSummary> {
  return request(`/api/usage?days=${days}`);
}

export type ExportFormat ="md" | "txt" | "docx" | "pdf" | "pptx" | "html";

/** "Download as": the server converts the answer (as shown) to a file → object URL + file name. */
export async function exportAnswer(
  jobId: string,
  format: ExportFormat,
  text: string,
): Promise<{ url: string; filename: string }> {
  const response = await apiFetch(`/api/jobs/${encodeURIComponent(jobId)}/export`, {
    method: "POST",
    body: JSON.stringify({ format, text }),
  });
  if (!response.ok) throw new Error(await readError(response));
  return { url: URL.createObjectURL(await response.blob()), filename: attachmentName(response, `answer.${format}`) };
}

/** The file name a download's Content-Disposition gives, else the fallback. */
function attachmentName(response: Response, fallback: string): string {
  const named = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(response.headers.get("Content-Disposition") || "");
  return named ? decodeURIComponent(named[1]) : fallback;
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
    response = await apiFetch(`/api/jobs/${encodeURIComponent(jobId)}/events`, { signal });
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
