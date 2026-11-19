"use client";

import { useEffect, useRef, useState } from "react";

import { useApp } from "@/components/app/AppProvider";
import PageHeader from "@/components/app/PageHeader";
import { useOpenChat } from "@/hooks/useOpenChat";
import { ApiError, deleteSchedule, listSchedules, runScheduleNow, setScheduleEnabled, type Schedule } from "@/lib/api";
import { errorText } from "@/lib/chat";
import { describeSchedule } from "@/lib/schedules";
import { btn } from "@/lib/ui";

/** Run now while another answer runs (one at a time per user): tried again every RETRY_MS, at most RETRY_TIMES
 *  (10 minutes), while the page is open — instead of the API's "already has 1 active job(s)" (ISSUES_LOG O110). */
const RETRY_MS = 10_000;
const RETRY_TIMES = 60;
const isBusy = (err: unknown) => err instanceof ApiError && err.status === 409 && /active job/i.test(err.message);

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");
/** How the last run went, in words (src/jobs/schedules.py last_status). */
const LAST_RUN: Record<Schedule["last_status"], string> = {
  ok: "something new", quiet: "nothing changed", failed: "failed", "": "",
};

/** /schedules (BACKLOG L26 H1): the questions the user set to repeat — how often, the next and last run, why one
 *  did not run — with Pause / Resume, Delete and a link to the chat the answers land in. New ones come from a
 *  chat's menu (Repeat…). */
export default function SchedulesPage() {
  const app = useApp();
  const { openChat } = useOpenChat();
  const [rows, setRows] = useState<Schedule[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [waiting, setWaiting] = useState<Set<number>>(new Set());
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach((t) => clearTimeout(t));       // leaving the page stops the waiting
  }, []);

  useEffect(() => {
    let cancelled = false;
    listSchedules()
      .then((r) => { if (!cancelled) setRows(r); })
      .catch((err: unknown) => { if (!cancelled) setError(errorText(err)); });
    return () => { cancelled = true; };
  }, []);
  if (!app.auth.user) return null; // the shell shows the login when signed out

  async function toggle(s: Schedule) {
    try {
      const saved = await setScheduleEnabled(s.id, !s.enabled);
      setRows((all) => (all || []).map((r) => (r.id === s.id ? saved : r)));
    } catch (err) {
      setError(errorText(err));
    }
  }

  function stopWaiting(id: number) {
    clearTimeout(timers.current.get(id));
    timers.current.delete(id);
    setWaiting((w) => { const next = new Set(w); next.delete(id); return next; });
  }

  async function runNow(s: Schedule, tries = 0) {
    try {
      const saved = await runScheduleNow(s.id);
      setRows((all) => (all || []).map((r) => (r.id === s.id ? saved : r)));
      setError(null);
      stopWaiting(s.id);
    } catch (err) {
      if (isBusy(err) && tries < RETRY_TIMES) {
        setWaiting((w) => new Set(w).add(s.id));
        timers.current.set(s.id, setTimeout(() => void runNow(s, tries + 1), RETRY_MS));
        return;
      }
      stopWaiting(s.id);
      setError(isBusy(err) ? "Your other answer is still running after 10 minutes — try Run now again later." : errorText(err));
    }
  }

  async function remove(s: Schedule) {
    if (!window.confirm(`Delete the schedule "${s.prompt}"? Answers already in the chat stay.`)) return;
    try {
      await deleteSchedule(s.id);
      setRows((all) => (all || []).filter((r) => r.id !== s.id));
    } catch (err) {
      setError(errorText(err));
    }
  }

  const threadOf = (s: Schedule) => app.history.threads.find((t) => t.id === s.thread_id);

  return (
    <PageHeader
      title="Scheduled"
      about="Questions asked again on a timetable — a check or a digest that comes to you. Each schedule has its own chat (🔁); runs only read and report."
    >
      {error ? <p className="mt-6 text-[0.9rem] text-danger-text">{error}</p> : null}
      {rows === null && !error ? <p className="mt-6 text-[0.9rem] text-muted">Loading…</p> : null}
      {rows !== null && rows.length === 0 ? (
        <p className="mt-6 text-[0.9rem] text-muted">
          Nothing scheduled yet. Good uses: a weekly security re-check of a server, a daily look at whether your sites
          are up, a Monday digest of news for your software. Open a chat&apos;s ⋮ menu in the sidebar and choose
          Repeat….
        </p>
      ) : null}
      <ul className="m-0 mt-4 flex list-none flex-col gap-2 p-0">
        {(rows || []).map((s) => {
          const thread = threadOf(s);
          return (
            <li key={s.id} className={`rounded-xl border border-line bg-panel px-4 py-3 ${s.enabled ? "" : "opacity-70"}`}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <p className="m-0 text-[0.92rem] text-text">{s.prompt}</p>
                  <p className="m-0 mt-1 text-[0.78rem] text-muted">
                    {describeSchedule(s)} ({s.time_zone}) · {s.enabled ? `next ${when(s.next_run_at)}` : "paused"}
                    {s.last_run_at ? ` · last ${when(s.last_run_at)}` : " · not run yet"}
                    {LAST_RUN[s.last_status] ? ` (${LAST_RUN[s.last_status]})` : ""}
                  </p>
                  {s.last_error ? <p className="m-0 mt-1 text-[0.78rem] text-danger-text">{s.last_error}</p> : null}
                </div>
                <div className="flex shrink-0 gap-2">
                  {thread ? (
                    <button type="button" className={btn("ghost", "sm")} onClick={() => openChat(thread)}>
                      Open “{s.chat_title || thread.title}”
                    </button>
                  ) : null}
                  {waiting.has(s.id) ? (
                    <button type="button" className={btn("sm")} onClick={() => stopWaiting(s.id)}
                      title="Another answer is running; this run starts when it ends. Click to stop waiting.">
                      Waiting for your other answer… ✕
                    </button>
                  ) : (
                    <button type="button" className={btn("sm")} onClick={() => runNow(s)}>Run now</button>
                  )}
                  <button type="button" className={btn("sm")} onClick={() => toggle(s)}>
                    {s.enabled ? "Pause" : "Resume"}
                  </button>
                  <button type="button" className={btn("danger", "sm")} onClick={() => remove(s)}>Delete</button>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </PageHeader>
  );
}
