"use client";

import { useEffect, useRef } from "react";

import { listUnreadResults, type UnreadResult } from "@/lib/api";
import { chatPath } from "@/lib/routes";

/** How often a signed-in browser asks for new scheduled results: schedules run at most hourly, so a minute's delay
 *  is nothing, and the check is one small query. */
const CHECK_MS = 60_000;
const COUNT_RE = /^\(\d+\) /;

const STATUS_TEXT: Record<string, string> = {
  failed: "The run failed: open the chat to see why.",
  ok: "Something changed since the last run.",
};

function notify(row: UnreadResult) {
  // browsers allow notifications only on https:// or localhost and after the user said yes (asked when a schedule
  // is saved, RepeatDialog); otherwise the sidebar dot and the tab title are the signal
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  const note = new Notification(row.title, { body: STATUS_TEXT[row.status] ?? "A new result.", tag: row.thread_id });
  note.onclick = () => {
    window.focus();
    window.location.assign(chatPath(row.thread_id));
  };
}

/** Scheduled results find the user (ISSUES_LOG O99): while signed in, check for chats with an unread result; on a new
 *  one, reload the chat list (its sidebar dot) and show a browser notification. The tab title counts them. Results
 *  already unread at sign-in are not announced again — their dots show them. */
export function useScheduledResults(signedIn: boolean, onNew: () => void) {
  const told = useRef<Set<string> | null>(null);
  const baseTitle = useRef<string | null>(null);

  useEffect(() => {
    baseTitle.current ??= document.title.replace(COUNT_RE, "");      // the app's own title (app/layout.tsx)
    const plain = baseTitle.current;
    if (!signedIn) {
      told.current = null;
      document.title = plain;
      return;
    }
    let stopped = false;
    async function check() {
      let rows: UnreadResult[];
      try {
        rows = await listUnreadResults();
      } catch {
        return;                     // offline or signed out: the next check tries again
      }
      if (stopped) return;
      const key = (r: UnreadResult) => `${r.thread_id}:${r.job_id}`;
      const fresh = told.current === null ? [] : rows.filter((r) => !told.current!.has(key(r)));
      told.current = new Set(rows.map(key));
      document.title = rows.length ? `(${rows.length}) ${plain}` : plain;
      if (fresh.length) {
        onNew();
        fresh.forEach(notify);
      }
    }
    void check();
    const timer = window.setInterval(check, CHECK_MS);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [signedIn, onNew]);
}
