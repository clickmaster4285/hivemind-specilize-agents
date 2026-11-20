"use client";

import { useCallback, useEffect, useState } from "react";

import { useApp } from "@/components/app/AppProvider";
import { endMySession, listMySessions, type MySession } from "@/lib/api";
import { errorText } from "@/lib/chat";
import { btn, table } from "@/lib/ui";

const when = (iso: string) => new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

/** My account → Sessions (BACKLOG L30 T2): where my account is signed in — device, address, since when, last
 *  active — and Sign out for one of them. Ending this one signs this browser out. */
export default function SessionsPanel() {
  const app = useApp();
  const [sessions, setSessions] = useState<MySession[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    () => listMySessions().then(setSessions).catch((err: unknown) => setError(errorText(err))),
    [],
  );
  useEffect(() => {
    let cancelled = false;
    listMySessions()
      .then((list) => !cancelled && setSessions(list))
      .catch((err: unknown) => !cancelled && setError(errorText(err)));
    return () => {
      cancelled = true;
    };
  }, []);

  async function end(s: MySession) {
    if (s.current) {
      if (confirm("Sign out of this browser?")) void app.auth.signOut();
      return;
    }
    try {
      await endMySession(s.id);
      await load();
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <>
      {error ? <p role="alert" className="m-0 text-xs text-danger">{error}</p> : null}
      {sessions?.length ? (
        <div className={table.wrap}>
          <table className={table.table}>
            <thead className={table.head}>
              <tr>
                <th className={table.th}>Device</th>
                <th className={`${table.th} hidden sm:table-cell`}>Address</th>
                <th className={`${table.th} hidden md:table-cell`}>Signed in</th>
                <th className={table.th}>Last active</th>
                <th className={table.th}><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((s) => (
                <tr key={s.id} className={table.row}>
                  <td className={table.td}>
                    {s.device}
                    {s.current ? <span className="ml-2 text-xs text-copper">this one</span> : null}
                  </td>
                  <td className={`${table.td} hidden sm:table-cell text-muted`}>{s.address || "—"}</td>
                  <td className={`${table.td} hidden md:table-cell whitespace-nowrap text-muted`}>{when(s.started_at)}</td>
                  <td className={`${table.td} whitespace-nowrap text-muted`}>{when(s.last_active_at)}</td>
                  <td className={`${table.td} text-right`}>
                    <button type="button" className={btn("ghost", "sm")} onClick={() => void end(s)}>Sign out</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : sessions ? (
        <p className="m-0 text-xs text-muted">No other sign-ins.</p>
      ) : null}
    </>
  );
}
