"use client";

import { useEffect, useState } from "react";

import { listCommandLog, type CommandLogRow } from "@/lib/api";
import { errorText } from "@/lib/chat";
import { formFieldCompact } from "@/lib/ui";

const SEARCH_PAUSE_MS = 300;
/** Decisions that mean the server was not changed: shown muted. */
const NOT_RUN = new Set(["cancelled", "refused"]);

/** Settings → Server commands (admins): what the agent ran (or was refused) on servers, for every user — the audit
 *  trail, kept after an account is deleted (ISSUES_LOG O123). Searchable by user, server, host or command. */
export default function CommandLogPanel() {
  const [search, setSearch] = useState("");
  const [rows, setRows] = useState<CommandLogRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      listCommandLog(search.trim())
        .then((r) => { if (!cancelled) { setRows(r.rows); setError(null); } })
        .catch((err: unknown) => { if (!cancelled) setError(errorText(err)); });
    }, SEARCH_PAUSE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [search]);

  return (
    <>
      <h4 className="m-0 text-base font-semibold [font-family:var(--font-display)]">Server commands</h4>
      <p className="m-0 text-xs text-muted">
        Every change the agent made, or was refused, on a server — who, where, what. Kept when an account is deleted.
      </p>
      <input
        className={`${formFieldCompact} mt-2 w-full max-w-sm`}
        placeholder="Search user, server or command"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        aria-label="Search the command log"
      />
      {error ? <p className="m-0 mt-2 text-xs text-danger">{error}</p> : null}
      {rows && !rows.length ? <p className="m-0 mt-2 text-xs text-muted">Nothing here.</p> : null}
      {rows?.length ? (
        <div className="mt-2 max-h-[70vh] overflow-auto">
          <table className="w-full border-collapse text-xs">
            <thead className="sticky top-0 bg-panel-2 text-left text-muted">
              <tr>
                <th className="py-1 pr-2 font-normal">When</th>
                <th className="py-1 pr-2 font-normal">Who</th>
                <th className="py-1 pr-2 font-normal">Server</th>
                <th className="py-1 pr-2 font-normal">Command</th>
                <th className="py-1 font-normal">Result</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className={`border-t border-line align-top ${NOT_RUN.has(r.decision) ? "text-muted" : ""}`}>
                  <td className="whitespace-nowrap py-1 pr-2 text-muted">{r.at ? new Date(r.at).toLocaleString() : "—"}</td>
                  <td className="whitespace-nowrap py-1 pr-2">
                    {r.username}
                    {r.deleted ? <span className="ml-1 text-muted">(deleted)</span> : null}
                  </td>
                  <td className="whitespace-nowrap py-1 pr-2" title={r.host}>{r.server}</td>
                  <td className="py-1 pr-2">
                    <details>
                      <summary className="cursor-pointer break-all font-[var(--font-mono)]">{r.command}</summary>
                      {r.output ? (
                        <pre className="m-0 mt-1 max-h-48 overflow-auto whitespace-pre-wrap text-muted">{r.output}</pre>
                      ) : (
                        <p className="m-0 mt-1 text-muted">No output kept.</p>
                      )}
                    </details>
                  </td>
                  <td className="whitespace-nowrap py-1">
                    {r.decision}
                    {r.exit_code !== null && r.exit_code !== 0 ? <span className="text-danger"> · exit {r.exit_code}</span> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </>
  );
}
