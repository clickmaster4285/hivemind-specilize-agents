/** The app's page addresses. A chat has its own address (/chat/<id>), so reload, back and a pasted link reopen it
 *  (BACKLOG L24 U1); "/" is a new chat. */

export const NEW_CHAT_PATH = "/";
export const PLUGINS_PATH = "/plugins";
export const SETTINGS_PATH = "/settings";
export const DEVICES_PATH = "/devices";
export const PROJECTS_PATH = "/projects";
export const LIBRARY_PATH = "/library";
export const SCHEDULES_PATH = "/schedules";
export const SHARED_PATH = "/shared";
const CHAT_PREFIX = "/chat/";

// the forms that are pages of their own inside the layout (BACKLOG L19b D5): "new" opens an empty form
export const NEW_DEVICE_PATH = `${DEVICES_PATH}/new`;
// a share link (BACKLOG L30 T4): opened while signed in, it lets that person read the chat
export const SHARE_LINK_PREFIX = "/s/";
export const shareLinkPath = (token: string) => `${SHARE_LINK_PREFIX}${encodeURIComponent(token)}`;
export const sharedChatPath = (id: string) => `${SHARED_PATH}/${encodeURIComponent(id)}`;
export const devicePath = (id: string) => `${DEVICES_PATH}/${encodeURIComponent(id)}`;
export type SettingsTab = "users" | "commands" | "usage" | "agents" | "knowledge" | "account";
export const settingsTabPath = (tab: SettingsTab) => `${SETTINGS_PATH}?tab=${tab}`;
export const userPath = (id: number | "new") => `${SETTINGS_PATH}/users/${id}`;
export const agentPath = (id: string) => `${SETTINGS_PATH}/agents/${encodeURIComponent(id)}`;

/** The own page a path belongs to: "/settings/users/3" → "/settings", so the sidebar and navbar name its page. */
export function pageRoot(pathname: string | null): string {
  return /^\/[^/?#]*/.exec(pathname || "")?.[0] ?? "";
}

/** The navbar title of each own page; a chat page shows the chat's title instead. */
export const PAGE_TITLES: Record<string, string> = {
  [SETTINGS_PATH]: "Settings",
  [PLUGINS_PATH]: "Plugins",
  [DEVICES_PATH]: "Devices",
  [PROJECTS_PATH]: "Projects",
  [LIBRARY_PATH]: "Library",
  [SCHEDULES_PATH]: "Scheduled",
  [SHARED_PATH]: "Shared with me",
};

export function chatPath(id: string): string {
  return `${CHAT_PREFIX}${encodeURIComponent(id)}`;
}

/** The chat a path shows: its id on /chat/<id>, null on "/" (a new chat), undefined on any other page. */
export function chatIdFromPath(pathname: string | null): string | null | undefined {
  if (!pathname || pathname === NEW_CHAT_PATH) return pathname ? null : undefined;
  if (!pathname.startsWith(CHAT_PREFIX)) return undefined;
  const id = decodeURIComponent(pathname.slice(CHAT_PREFIX.length).replace(/\/+$/, ""));
  return id || null;
}
