"use client";

import { useEffect, useMemo, useState } from "react";

import { MODE_LABEL } from "@/components/settings/DeviceForm";
import { getDeviceShares, listUsers, setDeviceShares, type DeviceShare } from "@/lib/api";
import { errorText } from "@/lib/chat";
import type { ManagedUser } from "@/lib/types";
import { btn, formFieldCompact } from "@/lib/ui";

const SHARE_MODES = ["audit", "confirm"] as const;     // never "trusted" on someone else's behalf (server_vault)
// a share row's "who": a person ("u:<id>") or a team ("t:<name>")
const whoOf = (s: DeviceShare) => (s.user_id !== null ? `u:${s.user_id}` : `t:${s.team}`);
const shareFor = (key: string): Pick<DeviceShare, "user_id" | "team"> =>
  key.startsWith("u:") ? { user_id: Number(key.slice(2)), team: null } : { user_id: null, team: key.slice(2) };

/** Devices → an admin's device → Sharing (BACKLOG L30 T4): who else may use it from chat, and how. They never see its
 *  password; changing it here once changes it for everyone. Saved as one list, all or none. */
export default function DeviceSharing({ deviceId }: { deviceId: string }) {
  const [rows, setRows] = useState<DeviceShare[] | null>(null);
  const [people, setPeople] = useState<ManagedUser[]>([]);
  const [note, setNote] = useState<{ text: string; bad: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getDeviceShares(deviceId), listUsers()])
      .then(([shares, users]) => {
        if (cancelled) return;
        setRows(shares);
        setPeople(users.filter((u) => u.status === "active"));
      })
      .catch((err: unknown) => !cancelled && setNote({ text: errorText(err), bad: true }));
    return () => {
      cancelled = true;
    };
  }, [deviceId]);

  const teams = useMemo(() => [...new Set(people.map((u) => u.team).filter(Boolean))].sort() as string[], [people]);
  const taken = new Set((rows ?? []).map(whoOf));
  const choices = [
    ...teams.map((t) => ({ key: `t:${t}`, label: `Team: ${t}` })),
    ...people.filter((u) => u.role !== "admin").map((u) => ({ key: `u:${u.id}`, label: u.username })),
  ];
  const next = choices.find((c) => !taken.has(c.key));

  function setRow(index: number, patch: Partial<DeviceShare>) {
    setRows((all) => (all ?? []).map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  function setWho(index: number, key: string) {
    setRow(index, shareFor(key));
  }

  async function save() {
    if (!rows) return;
    setBusy(true);
    setNote(null);
    try {
      setRows(await setDeviceShares(deviceId, rows));
      setNote({ text: rows.length ? "Saved — they see it in their Devices list." : "Saved — shared with no one.", bad: false });
    } catch (err) {
      setNote({ text: errorText(err), bad: true });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mt-4 flex flex-col gap-3 rounded-xl border border-line bg-panel p-5">
      <div>
        <h2 className="m-0 text-base font-semibold [font-family:var(--font-display)]">Sharing</h2>
        <p className="m-0 mt-1 text-xs text-muted">
          Let people or a whole team use this device from chat. They never see its password; when you change it here,
          it changes for everyone. Every command they run on it goes in the Server commands log, with who asked.
        </p>
      </div>
      {rows === null ? null : rows.length ? (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {rows.map((r, i) => (
            <li key={whoOf(r)} className="flex flex-wrap items-center gap-2">
              <select className={`${formFieldCompact} min-w-48`} value={whoOf(r)} aria-label="Shared with"
                      onChange={(e) => setWho(i, e.target.value)}>
                {choices.filter((c) => c.key === whoOf(r) || !taken.has(c.key)).map((c) => (
                  <option key={c.key} value={c.key}>{c.label}</option>
                ))}
              </select>
              <select className={formFieldCompact} value={r.mode} aria-label="What the agent may do"
                      onChange={(e) => setRow(i, { mode: e.target.value as DeviceShare["mode"] })}>
                {SHARE_MODES.map((m) => <option key={m} value={m}>{MODE_LABEL[m]}</option>)}
              </select>
              <button type="button" className={btn("ghost", "sm")}
                      onClick={() => setRows((all) => (all ?? []).filter((_, j) => j !== i))}>Remove</button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="m-0 text-xs text-muted">Only you can use it.</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={btn("ghost", "sm")} disabled={!next || rows === null}
                onClick={() => next && setRows((all) => [...(all ?? []), { ...shareFor(next.key), mode: "audit" }])}>
          Add a person or team
        </button>
        <button type="button" className={btn("primary", "sm")} disabled={busy || rows === null} onClick={() => void save()}>
          {busy ? "Saving…" : "Save sharing"}
        </button>
        {note ? <span role="status" className={`text-xs ${note.bad ? "text-danger" : "text-muted"}`}>{note.text}</span> : null}
      </div>
    </section>
  );
}
