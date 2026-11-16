"use client";

import { useCallback, useEffect, useState } from "react";

import { getThreadMemory } from "@/lib/api";
import { chatMemory, type ChatMemory } from "@/lib/chat";

/** A saved chat's memory, or null when it cannot load (a new chat has no row yet: no meter). */
function load(threadId: string): Promise<ChatMemory | null> {
  return getThreadMemory(threadId).then(chatMemory).catch(() => null);
}

/** The open chat's memory as the server measures it (O76), reloaded when another chat opens and after each answer
 *  (when `running` turns false); `refresh` after /compact. */
export function useThreadMemory(threadId: string | null, running: boolean) {
  const [loaded, setLoaded] = useState<{ threadId: string; memory: ChatMemory | null } | null>(null);

  const refresh = useCallback(async () => {
    if (threadId) setLoaded({ threadId, memory: await load(threadId) });
  }, [threadId]);

  useEffect(() => {
    if (running || !threadId) return;
    let cancelled = false;
    void load(threadId).then((memory) => {
      if (!cancelled) setLoaded({ threadId, memory });
    });
    return () => {
      cancelled = true;
    };
  }, [threadId, running]);

  // another chat's numbers never show for this one
  return { memory: loaded && loaded.threadId === threadId ? loaded.memory : null, refresh };
}
