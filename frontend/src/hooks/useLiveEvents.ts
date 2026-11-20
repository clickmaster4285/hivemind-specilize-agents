import { useEffect, useRef } from "react";

import { streamLiveEvents } from "@/lib/api";
import { LIVE_TOPICS, emitLive, isLiveTopic, onLive, type LiveTopic } from "@/lib/liveEvents";

const RETRY_FIRST_MS = 1000;
const RETRY_MAX_MS = 30_000;
// notices that come together (a chat started and its title saved) cause one reload, not several
const SETTLE_MS = 300;

/** The app-wide live stream (BACKLOG L29): open while signed in, one per tab. A dropped stream reconnects (1 s,
 *  doubling to 30 s); after a reconnect every list reloads once, since notices may have been missed meanwhile. */
export function useLiveEvents(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let wait = RETRY_FIRST_MS;
    let connectedBefore = false;
    let timer: number | undefined;

    const connect = async () => {
      try {
        await streamLiveEvents((type) => {
          if (type === "hello") {
            if (connectedBefore) LIVE_TOPICS.forEach(emitLive);
            connectedBefore = true;
            wait = RETRY_FIRST_MS;
          } else if (isLiveTopic(type)) {
            emitLive(type);
          }
        }, controller.signal);
      } catch {
        // the API restarted, the network dropped, or the sign-in ended (apiFetch then signs the page out)
      }
      if (controller.signal.aborted) return;
      timer = window.setTimeout(() => void connect(), wait);
      wait = Math.min(wait * 2, RETRY_MAX_MS);
    };
    void connect();
    return () => {
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [active]);
}

/** Reload something when one of `topics` is announced (`active` = this list is shown). */
export function useLiveTopic(topics: readonly LiveTopic[], reload: () => unknown, active = true): void {
  const latest = useRef(reload);
  useEffect(() => {
    latest.current = reload;
  });
  const key = topics.join(",");
  useEffect(() => {
    if (!active) return;
    let timer: number | undefined;
    const off = onLive(key.split(",") as LiveTopic[], () => {
      if (timer !== undefined) window.clearTimeout(timer);
      timer = window.setTimeout(() => void latest.current(), SETTLE_MS);
    });
    return () => {
      off();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [key, active]);
}
