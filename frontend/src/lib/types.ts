export type RunMode = "auto" | "chat" | "agentic";

/** A domain specialist (src/specialists.py): the assistant picks one from `covers`, or the user pins one. */
export type Specialist = {
  id: string;
  name: string;
  covers: string;
  instructions: string;
  tools: string[];
  collections: string[];
  enabled: boolean;
  sort_order: number;
  accent: string;
};

export type SpecialistInput = Omit<Specialist, "id" | "sort_order">;

/** What the agent may change on a server (src/server_vault.py MODES). */
export type ServerMode = "audit" | "confirm" | "trusted";

/** A Linux server login kept in the API's memory for this session (src/server_vault.py) — never the secret. */
export type ServerInfo = {
  id: string;
  label: string;
  host: string;
  port: number;
  username: string;
  auth: "password" | "key";
  host_key: string;
  mode: ServerMode;
  expires_at: number;
  os?: string;
  os_family?: "linux" | "windows";
  access?: string;
};

export type ServerLoginInput = {
  label: string;
  host: string;
  port: number;
  username: string;
  password?: string;
  private_key?: string;
  key_passphrase?: string;
  mode: ServerMode;
};

export type SpecialistOptions = {
  tools: { name: string; description: string }[];
  collections: string[];
};

export type Health = {
  ok: boolean;
  llm_reachable: boolean;
  model: string;
  base_url: string;
  queue_depth?: number;
  estimated_wait_seconds?: number;
  llm_slots?: number;
  image_slots?: number;
  image_profile?: string;
  image_pipeline?: {
    state?: string;
    error?: string | null;
    loaded?: boolean;
    path?: string | null;
  };
  auth_required?: boolean;
};

export type AuthUser = {
  id: number;
  username: string;
  role: string;
  daily_job_quota?: number;
};

export type ModelInfo = {
  name: string;
  backend: string;
  vram_class: string;
  role: string;
  max_concurrency: number;
  enabled: boolean;
  vision?: boolean;
  image_gen?: boolean;
  available?: boolean;
};

export type ChatImage = {
  url: string;
  filename: string;
  mime: string;
};

export type Job = {
  id: string;
  user_id: number;
  mode: RunMode;
  status: string;
  priority: number;
  query: string;
  /** Which chat this job belongs to; lets history regroup a conversation after a reload */
  thread_id?: string | null;
  payload: Record<string, unknown>;
  model_plan: Record<string, string>;
  error?: string | null;
  log_path?: string | null;
  created_at?: string | null;
  deleted_at?: string | null;
};

export type JobEvent = {
  type: string;
  [key: string]: unknown;
};

export type ToolStep = {
  id: string;
  tool: string;
  args?: string;
  result?: string;
  error?: boolean;
};

export type JobArtifact = {
  filename: string;
  mime?: string;
  relpath?: string;
  bytes?: number;
  kind?: string;
  jobId?: string;
};

/** A knowledge-base passage an answer was built from (numbered like the [n] citations). */
export type BrainSource = {
  n: number;
  title: string;
  url?: string | null;
  heading_path?: string | null;
  publisher?: string | null;
  updated_at?: string | null;
  source: string;
  cited: boolean;
  /** When the knowledge-base source was last checked for changes; refreshing = being re-checked now */
  checked_at?: string | null;
  refreshing?: boolean;
};

/** The check after a knowledge-base answer (src/brain/verify.py): which cited sentences the sources confirm. */
export type AnswerVerification = {
  checked: number;
  supported: number;
  unsupported: { text: string; cites: number[] }[];
  unclear: { text: string; cites: number[]; verdict: string }[];
  seconds?: number;
};

export type FeedItem = {
  id: string;
  kind: "user" | "agent" | "system";
  title: string;
  meta?: string;
  body: string;
  accent?: string;
  pending?: boolean;
  agentId?: string;
  /** Collapsed tool activity attached to an agent step */
  tools?: ToolStep[];
  images?: ChatImage[];
  jobId?: string;
  imageFiles?: { filename: string; mime?: string }[];
  artifacts?: JobArtifact[];
  /** Knowledge-base answers: true = built from sources; false = brain had nothing relevant */
  grounded?: boolean;
  sources?: BrainSource[];
  verification?: AnswerVerification;
  /** operate_server: a command waiting for the user to approve or cancel. */
  confirm?: { commandId: string; command: string; server: string; answered?: "run" | "cancel" };
  /** While running: when the step started (ms), what it is doing now, and whether text is streaming in */
  startedAt?: number;
  statusText?: string;
  steps?: string[];        // the procedure so far ("Searching the web…", "Reading 5 pages…") — shown while running
  streaming?: boolean;
};
