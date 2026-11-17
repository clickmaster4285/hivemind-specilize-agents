"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

import { useApp } from "@/components/app/AppProvider";
import { chatIdFromPath, chatPath, NEW_CHAT_PATH } from "@/lib/routes";

/**
 * Keeps the address and the open chat in step (BACKLOG L24 U1). Renders nothing.
 * - Address → chat: on load, back / forward or a pasted link, /chat/<id> opens that chat and "/" starts a new one.
 * - Chat → address: opening a chat, starting one, or a new chat's first message puts /chat/<id> in the address
 *   with history.pushState (Next keeps usePathname in step), so the chat screen stays mounted mid-answer.
 * Only on the chat pages; Settings and the other pages keep their own address.
 */
export default function ChatUrlSync() {
  const app = useApp();
  const router = useRouter();
  const pathname = usePathname();
  const { history, chat } = app;
  const urlId = chatIdFromPath(pathname);
  const activeId = history.activeThreadId;
  const active = activeId ? history.threads.find((t) => t.id === activeId) : undefined;
  const activeSaved = Boolean(active && active.jobIds.length > 0);   // a chat with no message yet stays at "/"
  // the address this component is bringing the chat to; until the chat matches it, the chat must not
  // push its own (older) address back
  const following = useRef<string | null>(null);
  const seen = useRef<string | null>(null);

  // address → chat
  useEffect(() => {
    if (!history.ready || urlId === undefined || seen.current === pathname) return;
    // after a reload the open-chat pointer is restored without its messages, so the first sync always opens
    const first = seen.current === null;
    seen.current = pathname;
    if (urlId === null) {
      if (activeSaved) {
        following.current = pathname;
        app.newChat();
      }
      return;
    }
    if (urlId === activeId && !first) return;
    const thread = history.threads.find((t) => t.id === urlId);
    if (thread) {
      following.current = pathname;
      void chat.openThread(thread);
    } else {
      router.replace(NEW_CHAT_PATH);              // an unknown or deleted chat: a new one instead of an empty page
    }
  }, [history.ready, urlId, pathname, activeId, activeSaved, history.threads, chat, app, router]);

  // chat → address
  useEffect(() => {
    if (!history.ready || urlId === undefined) return;
    const shows = urlId === null ? !activeSaved : urlId === activeId;
    if (shows) {
      following.current = null;
      return;
    }
    if (following.current === pathname) return;   // still opening what the address names
    const want = activeSaved && activeId ? chatPath(activeId) : NEW_CHAT_PATH;
    seen.current = want;                           // our own change: nothing to open when the path updates
    window.history.pushState(null, "", want);
  }, [history.ready, urlId, pathname, activeId, activeSaved]);

  return null;
}
