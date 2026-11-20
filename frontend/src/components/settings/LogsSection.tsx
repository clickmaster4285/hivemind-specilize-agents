"use client";

import { useEffect, useState } from "react";

import CommandLogPanel from "@/components/settings/users/CommandLogPanel";
import SecurityLogPanel from "@/components/settings/users/SecurityLogPanel";
import { getAppLog, getSystemHealth, type AppLogLine, type HealthCheck } from "@/lib/api";
import { errorText } from "@/lib/chat";
import { btn, table } from "@/lib/ui";

type View = "health" | "app" | "security" | "commands";
const VIEWS: { id: View; label: string }[] = [
  { id: "health", label: "Health & alerts" },
  { id: "app", label: "App log" },
  { id: "security", label: "Security" },
  { id: "commands", label: "Server commands" },
];
const DOT: Record<HealthCheck["status"], string> = { ok: "bg-teal", warn: "bg-copper", bad: "bg-danger" };
const LEVEL: Record<AppLogLine["level"], string> = { warning: "text-copper", error: "text-danger", critical: "text-danger" };

/** Settings → Logs (BACKLOG L30 T5; admins — the user's choice: alerts here, not by email): every part of the system
 *  ok / warn / bad with what to do, the app's warnings and errors, the security log and the server commands. */
export default function LogsSection() {
  const [view, setView] = useState<View>("health");
  return (
    <div className="flex flex-col gap-3">
      <h4 className="m-0 text-base font-semibold [font-family:var(--font-display)]">Logs</h4>
      <div className="flex flex-wrap gap-1" role="tablist">
        {VIEWS.map((v) => (
          <button key={v.id} type="button" role="tab" aria-selected={view === v.id} onClick={() => setView(v.id)}
                  className={`rounded-full border px-3 py-1 text-xs ${view === v.id ? "border-copper text-text" : "border-line text-muted hover:text-text"}`}>
            {v.label}
          </button>
        ))}
      </div>
      {view === "health" ? <Health /> : null}
      {view === "app" ? <AppLog /> : null}
      {view === "security" ? <SecurityLogPanel /> : null}
      {view === "commands" ? <CommandLogPanel /> : null}
    </div>
  );
}

function Health() {
  const [checks, setChecks] = useState<HealthCheck[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [round, setRound] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getSystemHealth()
      .then((r) => !cancelled && setChecks(r.checks))
      .catch((err: unknown) => !cancelled && setError(errorText(err)));
    return () => {
      cancelled = true;
    };
  }, [round]);

  const problems = checks?.filter((c) => c.status !== "ok").length ?? 0;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <p className="m-0 text-xs text-muted">
          {checks ? (problems ? `${problems} thing${problems === 1 ? "" : "s"} to look at.` : "Everything is fine.") : "Checking…"}
        </p>
        <button type="button" className={btn("ghost", "sm")} onClick={() => setRound((n) => n + 1)}>Check again</button>
      </div>
      {error ? <p role="alert" className="m-0 text-xs text-danger">{error}</p> : null}
      <ul className="m-0 grid list-none grid-cols-1 gap-2 p-0 md:grid-cols-2">
        {(checks ?? []).map((c) => (
          <li key={c.name} className={`rounded-lg border bg-panel-2 px-3 py-2.5 ${c.status === "bad" ? "border-danger" : "border-line"}`}>
            <span className="flex items-center gap-2 text-sm font-semibold">
              <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${DOT[c.status]}`} aria-label={c.status} />
              {c.name}
            </span>
            <span className="mt-0.5 block text-xs text-muted">{c.detail}</span>
            {c.fix ? <span className="mt-1 block text-xs">What to do: <code className="text-[0.75rem]">{c.fix}</code></span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function AppLog() {
  const [level, setLevel] = useState<"warning" | "error">("warning");
  const [lines, setLines] = useState<AppLogLine[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [round, setRound] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getAppLog(level)
      .then((r) => !cancelled && setLines(r.lines))
      .catch((err: unknown) => !cancelled && setError(errorText(err)));
    return () => {
      cancelled = true;
    };
  }, [level, round]);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <select className={btn("ghost", "sm")} value={level} onChange={(e) => setLevel(e.target.value as "warning" | "error")}
                aria-label="Level">
          <option value="warning">Warnings and errors</option>
          <option value="error">Errors only</option>
        </select>
        <button type="button" className={btn("ghost", "sm")} onClick={() => setRound((n) => n + 1)}>Refresh</button>
        <span className="text-muted">From the API&rsquo;s log (logs/api.log), newest first.</span>
      </div>
      {error ? <p role="alert" className="m-0 text-xs text-danger">{error}</p> : null}
      {lines && !lines.length ? <p className="m-0 text-xs text-muted">Nothing here — no warnings or errors lately.</p> : null}
      {lines?.length ? (
        <div className={`${table.wrap} max-h-[70vh] overflow-auto`}>
          <table className={`${table.table} text-xs`}>
            <thead className={`${table.head} sticky top-0`}>
              <tr>
                <th className={table.th}>When</th>
                <th className={table.th}>Level</th>
                <th className={`${table.th} hidden md:table-cell`}>Where</th>
                <th className={table.th}>Message</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l, i) => (
                <tr key={`${l.at}-${i}`} className={`${table.row} align-top`}>
                  <td className={`${table.td} whitespace-nowrap text-muted`}>{l.at}</td>
                  <td className={`${table.td} ${LEVEL[l.level]}`}>{l.level}</td>
                  <td className={`${table.td} hidden md:table-cell font-[var(--font-mono)] text-muted`}>{l.where}</td>
                  <td className={`${table.td} break-words`}>{l.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
