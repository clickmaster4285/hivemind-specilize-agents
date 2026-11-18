"use client";

import { useEffect, useState } from "react";

import { useApp } from "@/components/app/AppProvider";
import PageHeader from "@/components/app/PageHeader";
import { useOpenChat } from "@/hooks/useOpenChat";
import { deleteSchedule, listSchedules, setScheduleEnabled, type Schedule } from "@/lib/api";
import { errorText } from "@/lib/chat";
import { describeSchedule } from "@/lib/schedules";
import { btn } from "@/lib/ui";

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");

/** /schedules (BACKLOG L26 H1): the questions the user set to repeat — how often, the next and last run, why one
 *  did not run — with Pause / Resume, Delete and a link to the chat the answers land in. New ones come from a
 *  chat's menu (Repeat…). */
export default function SchedulesPage() {
  const app = useApp();
  const { openChat } = useOpenChat();
  const [rows, setRows] = useState<Schedule[] | null>(null);
  const [error, setError] = useState<string | null>(null);

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
      about="Questions asked again on a timetable. Each answer lands in its chat; scheduled runs only read and report."
    >
      {error ? <p className="mt-6 text-[0.9rem] text-danger-text">{error}</p> : null}
      {rows === null && !error ? <p className="mt-6 text-[0.9rem] text-muted">Loading…</p> : null}
      {rows !== null && rows.length === 0 ? (
        <p className="mt-6 text-[0.9rem] text-muted">
          Nothing scheduled yet. Open a chat&apos;s ⋮ menu in the sidebar and choose Repeat….
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
                    {s.last_run_at ? ` · last ${when(s.last_run_at)}` : ""}
                  </p>
                  {s.last_error ? <p className="m-0 mt-1 text-[0.78rem] text-danger-text">{s.last_error}</p> : null}
                </div>
                <div className="flex shrink-0 gap-2">
                  {thread ? (
                    <button type="button" className={btn("ghost", "sm")} onClick={() => openChat(thread)}>
                      Open “{s.chat_title || thread.title}”
                    </button>
                  ) : null}
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
