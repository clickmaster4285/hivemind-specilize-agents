/** Live notices inside the page (BACKLOG L29): the one stream (hooks/useLiveEvents) re-emits each notice here,
 *  and every list that follows a topic (hooks/useLiveTopic) reloads itself. A notice names a topic only — the
 *  list asks the API again for its data. */

export type LiveTopic = "users" | "signup" | "security" | "devices" | "chats" | "projects" | "schedules";
export const LIVE_TOPICS: readonly LiveTopic[] = ["users", "signup", "security", "devices", "chats", "projects",
                                                  "schedules"];

const bus: EventTarget | null = typeof window !== "undefined" ? new EventTarget() : null;

export function isLiveTopic(type: string): type is LiveTopic {
  return (LIVE_TOPICS as readonly string[]).includes(type);
}

export function emitLive(topic: LiveTopic): void {
  bus?.dispatchEvent(new Event(topic));
}

/** Call `fn` when any of `topics` is announced; returns the unsubscribe. */
export function onLive(topics: readonly LiveTopic[], fn: () => void): () => void {
  if (!bus) return () => undefined;
  for (const t of topics) bus.addEventListener(t, fn);
  return () => {
    for (const t of topics) bus.removeEventListener(t, fn);
  };
}
