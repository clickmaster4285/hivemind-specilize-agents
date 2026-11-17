/** The app's page addresses. A chat has its own address (/chat/<id>), so reload, back and a pasted link reopen it
 *  (BACKLOG L24 U1); "/" is a new chat. */

export const NEW_CHAT_PATH = "/";
export const PLUGINS_PATH = "/plugins";
export const SETTINGS_PATH = "/settings";
const CHAT_PREFIX = "/chat/";

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
