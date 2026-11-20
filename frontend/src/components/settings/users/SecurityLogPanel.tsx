"use client";

import { useEffect, useState } from "react";

import { useLiveTopic } from "@/hooks/useLiveEvents";

import { listSecurityEvents } from "@/lib/api";
import type { SecurityLog } from "@/lib/types";
import { btn } from "@/lib/ui";

/** What each kind of event means, in words (src/core/security_log.py KINDS). */
const KIND_LABEL: Record<string, string> = {
  login_failed: "Wrong password",
  account_locked: "Account locked",
  address_blocked: "Computer blocked",
  token_reused: "Sign-in token reused",
  upload_refused: "Upload refused",
  unlocked: "Unlocked by an admin",
  account_deleted: "Account deleted",
};
/** Kinds worth a second look: shown in the danger colour. */
const SERIOUS = new Set(["account_locked", "address_blocked", "token_reused"]);

const who = (e: SecurityLog["events"][number]) =>
  e.username ?? (e.account.startsWith("name:") ? `“${e.account.slice(5)}” (no such account)` : e.account || "—");

/** Settings → Users: the security log (BACKLOG L16 S6) — failed sign-ins, locks, a sign-in token used twice (a copied
 *  cookie: that session was ended), refused uploads, unlocks. Folded until opened; read when opened. */
export default function SecurityLogPanel() {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState("");
  const [log, setLog] = useState<SecurityLog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fresh, setFresh] = useState(0);          // a new entry was logged (live notice, BACKLOG L29)
  useLiveTopic(["security"], () => setFresh((n) => n + 1), open);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    listSecurityEvents(kind || undefined)
      .then((r) => { if (!cancelled) { setLog(r); setError(null); } })
      .catch((err: unknown) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)); });
    return () => { cancelled = true; };
  }, [open, kind, fresh]);

  return (
    <details
      className="mt-4 rounded-lg border border-line bg-panel-2 p-3"
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
    >
      <summary className="cursor-pointer text-sm font-semibold">Security log</summary>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        <select className={btn("ghost", "sm")} value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind">
          <option value="">Everything</option>
          {(log?.kinds ?? Object.keys(KIND_LABEL)).map((k) => (
            <option key={k} value={k}>{KIND_LABEL[k] ?? k}</option>
          ))}
        </select>
        {log ? <span className="text-muted">Kept {log.kept_days} days · newest first</span> : null}
      </div>
      {error ? <p className="m-0 mt-2 text-xs text-danger">{error}</p> : null}
      {log && !log.events.length ? <p className="m-0 mt-2 text-xs text-muted">Nothing here.</p> : null}
      {log?.events.length ? (
        <div className="mt-2 max-h-80 overflow-auto">
          <table className="w-full border-collapse text-xs">
            <thead className="sticky top-0 bg-panel-2 text-left text-muted">
              <tr>
                <th className="py-1 pr-2 font-normal">When</th>
                <th className="py-1 pr-2 font-normal">What</th>
                <th className="py-1 pr-2 font-normal">Account</th>
                <th className="py-1 pr-2 font-normal">From</th>
                <th className="py-1 font-normal">Detail</th>
              </tr>
            </thead>
            <tbody>
              {log.events.map((e) => (
                <tr key={e.id} className="border-t border-line align-top">
                  <td className="whitespace-nowrap py-1 pr-2 text-muted">{new Date(e.at).toLocaleString()}</td>
                  <td className={`whitespace-nowrap py-1 pr-2 ${SERIOUS.has(e.kind) ? "text-danger" : ""}`}>
                    {KIND_LABEL[e.kind] ?? e.kind}
                  </td>
                  <td className="py-1 pr-2">{who(e)}</td>
                  <td className="whitespace-nowrap py-1 pr-2 text-muted">{e.address.replace(/^addr:/, "") || "—"}</td>
                  <td className="py-1 text-muted">{e.detail || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </details>
  );
}
