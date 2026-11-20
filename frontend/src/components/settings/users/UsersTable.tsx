"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { useApp } from "@/components/app/AppProvider";
import type { UsersApi } from "@/hooks/useUsers";
import type { UserBulkChanges } from "@/lib/api";
import { errorText } from "@/lib/chat";
import { userPath } from "@/lib/routes";
import type { ManagedUser } from "@/lib/types";
import { btn, formFieldCompact, table } from "@/lib/ui";

type SortKey = "name" | "last" | "queries";
type Sort = { key: SortKey; desc: boolean };

const SORT_VALUE: Record<SortKey, (u: ManagedUser) => string | number> = {
  name: (u) => u.username.toLowerCase(),
  last: (u) => (u.last_login_at ? Date.parse(u.last_login_at) : 0),
  queries: (u) => u.query_count ?? 0,
};
const ALL = "";

function lastSignIn(iso: string | null): string {
  return iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "never";
}

/** Settings → Users: the accounts (not sign-ups waiting) — search, filter, sort, and actions on one or many
 *  (BACKLOG L30 T2). A bulk change is one request, all or none (POST /api/auth/users/bulk). */
export default function UsersTable({ users, accounts }: { users: UsersApi; accounts: ManagedUser[] }) {
  const me = useApp().auth.user;
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState(ALL);
  const [role, setRole] = useState(ALL);
  const [team, setTeam] = useState(ALL);
  const [sort, setSort] = useState<Sort>({ key: "name", desc: false });
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; bad: boolean } | null>(null);

  const teams = useMemo(() => [...new Set(accounts.map((u) => u.team).filter(Boolean))].sort() as string[], [accounts]);
  const shown = useMemo(() => {
    const words = search.trim().toLowerCase();
    const value = SORT_VALUE[sort.key];
    return accounts
      .filter((u) => (!status || u.status === status) && (!role || u.role === role) && (!team || u.team === team))
      .filter((u) => !words || [u.username, u.email, u.team].some((f) => f?.toLowerCase().includes(words)))
      .sort((a, b) => {
        const [x, y] = [value(a), value(b)];
        return (x < y ? -1 : x > y ? 1 : 0) * (sort.desc ? -1 : 1);
      });
  }, [accounts, search, status, role, team, sort]);
  // only rows still shown count as picked: a filter change never acts on hidden people
  const chosen = shown.filter((u) => picked.has(u.id));

  async function act(work: () => Promise<string>) {
    setBusy(true);
    setNotice(null);
    try {
      setNotice({ text: await work(), bad: false });
    } catch (err) {
      setNotice({ text: errorText(err), bad: true });
    } finally {
      setBusy(false);
    }
  }

  function bulk(changes: UserBulkChanges, what: string) {
    const ids = chosen.map((u) => u.id);
    void act(async () => {
      const done = await users.bulk(ids, changes);
      setPicked(new Set());
      return `${what}: ${done.changed} of ${done.selected} changed.`;
    });
  }

  function askBulk(question: string, apply: (answer: string) => UserBulkChanges | null, what: string) {
    const answer = prompt(question);
    if (answer === null) return;
    const changes = apply(answer.trim());
    if (changes) bulk(changes, what);
    else setNotice({ text: "That is not a valid value — nothing was changed.", bad: true });
  }

  function toggleActive(u: ManagedUser) {
    if (u.status === "disabled") {
      void act(async () => {
        await users.update(u.id, { status: "active" });
        return `${u.username} can sign in again.`;
      });
      return;
    }
    if (!confirm(`Deactivate ${u.username}? They can no longer sign in; their chats and queries are kept, and you can activate them again.`)) return;
    void act(async () => {
      await users.deactivate(u.id);
      return `${u.username} is deactivated.`;
    });
  }

  function remove(u: ManagedUser) {
    const typed = prompt(
      `Delete ${u.username}?\n\nThey can never sign in again, and their name, email, devices, schedules and own skills ` +
        `are removed. Their chats are kept, anonymised. To only pause their access, use Deactivate instead.` +
        `\n\nType the username to confirm:`,
    );
    if (typed === null) return;
    if (typed.trim() !== u.username) {
      setNotice({ text: "The username did not match — nothing was deleted.", bad: true });
      return;
    }
    void act(async () => {
      const done = await users.remove(u.id, typed.trim());
      const kb = done.knowledge_base.startsWith("not removed") ? ` Knowledge base: ${done.knowledge_base}.` : "";
      return `${done.username} was deleted; their ${done.questions} questions are kept as ${done.kept_as}.${kb}`;
    });
  }

  const sortHead = (key: SortKey, text: string, className = "") => (
    <th className={`${table.th} ${className}`} aria-sort={sort.key === key ? (sort.desc ? "descending" : "ascending") : "none"}>
      <button type="button" className="uppercase tracking-[0.04em] hover:text-text"
              onClick={() => setSort((s) => ({ key, desc: s.key === key ? !s.desc : key !== "name" }))}>
        {text}
        {sort.key === key ? (sort.desc ? " ↓" : " ↑") : ""}
      </button>
    </th>
  );
  const allShownPicked = shown.length > 0 && chosen.length === shown.length;

  if (!accounts.length) return <p className="text-xs text-muted">No accounts yet.</p>;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <input className={`${formFieldCompact} min-w-[12rem] flex-1`} placeholder="Search name, email or team"
               value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search users" />
        <select className={formFieldCompact} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
          <option value={ALL}>Any status</option>
          <option value="active">Active</option>
          <option value="disabled">Disabled</option>
        </select>
        <select className={formFieldCompact} value={role} onChange={(e) => setRole(e.target.value)} aria-label="Role">
          <option value={ALL}>Any role</option>
          <option value="admin">Admins</option>
          <option value="member">Members</option>
        </select>
        {teams.length ? (
          <select className={formFieldCompact} value={team} onChange={(e) => setTeam(e.target.value)} aria-label="Team">
            <option value={ALL}>Any team</option>
            {teams.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        ) : null}
      </div>

      {chosen.length ? (
        <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-copper bg-panel-2 px-3 py-2 text-xs">
          <strong className="mr-1">{chosen.length} selected</strong>
          <button type="button" className={btn("ghost", "sm")} disabled={busy}
                  onClick={() => bulk({ status: "active" }, "Activated")}>Activate</button>
          <button type="button" className={btn("ghost", "sm")} disabled={busy}
                  onClick={() => confirm(`Deactivate ${chosen.length} accounts? They can no longer sign in.`) &&
                    bulk({ status: "disabled" }, "Deactivated")}>Deactivate</button>
          <button type="button" className={btn("ghost", "sm")} disabled={busy}
                  onClick={() => askBulk("Team for the selected accounts (empty clears it):",
                                         (t) => ({ team: t }), "Team set")}>Set team…</button>
          <button type="button" className={btn("ghost", "sm")} disabled={busy}
                  onClick={() => askBulk("Questions per day for the selected accounts:",
                                         (q) => (/^\d+$/.test(q) ? { daily_job_quota: Number(q) } : null),
                                         "Quota set")}>Set daily quota…</button>
          <button type="button" className={`${btn("ghost", "sm")} ml-auto`} onClick={() => setPicked(new Set())}>
            Clear
          </button>
        </div>
      ) : null}
      {notice ? (
        <p role="status" className={`m-0 text-xs ${notice.bad ? "text-danger" : "text-muted"}`}>{notice.text}</p>
      ) : null}

      <div className={table.wrap}>
        <table className={table.table}>
          <thead className={table.head}>
            <tr>
              <th className={`${table.th} w-8`}>
                <input type="checkbox" aria-label="Select every shown account" checked={allShownPicked}
                       onChange={(e) => setPicked(new Set(e.target.checked ? shown.map((u) => u.id) : []))} />
              </th>
              {sortHead("name", "User")}
              <th className={`${table.th} hidden sm:table-cell`}>Role</th>
              <th className={`${table.th} hidden md:table-cell`}>Team</th>
              <th className={`${table.th} hidden lg:table-cell`}>Modules</th>
              {sortHead("last", "Last sign-in", "hidden md:table-cell")}
              {sortHead("queries", "Queries", "hidden md:table-cell text-right")}
              <th className={`${table.th} hidden lg:table-cell text-right`}>Daily quota</th>
              <th className={table.th}><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {shown.map((u) => (
              <tr key={u.id} className={`${table.row} ${u.status === "disabled" ? "opacity-60" : ""}`}>
                <td className={table.td}>
                  <input type="checkbox" aria-label={`Select ${u.username}`} checked={picked.has(u.id)}
                         onChange={(e) => setPicked((p) => {
                           const next = new Set(p);
                           if (e.target.checked) next.add(u.id);
                           else next.delete(u.id);
                           return next;
                         })} />
                </td>
                <td className={table.td}>
                  <span className="flex items-center gap-2 font-semibold">
                    {u.username}
                    {u.status === "disabled" ? (
                      <span className="rounded-full border border-line px-1.5 py-0.5 text-[0.65rem] font-normal text-muted">disabled</span>
                    ) : null}
                    {u.locked_until ? (
                      <span className="rounded-full border border-danger px-1.5 py-0.5 text-[0.65rem] font-normal text-danger"
                            title={`${u.failed_logins} failed passwords — waits until ${new Date(u.locked_until).toLocaleTimeString()}`}>
                        locked
                      </span>
                    ) : null}
                  </span>
                  {u.email ? <small className="block text-xs text-muted">{u.email}</small> : null}
                  <small className="block text-xs text-muted md:hidden">
                    {u.role}{u.team ? ` · ${u.team}` : ""} · last sign-in {lastSignIn(u.last_login_at)}
                  </small>
                </td>
                <td className={`${table.td} hidden sm:table-cell capitalize`}>{u.role}</td>
                <td className={`${table.td} hidden md:table-cell text-muted`}>{u.team || "—"}</td>
                <td className={`${table.td} hidden lg:table-cell text-muted`}>{u.modules.length ? u.modules.join(", ") : "chat only"}</td>
                <td className={`${table.td} hidden md:table-cell whitespace-nowrap text-muted`}>{lastSignIn(u.last_login_at)}</td>
                <td className={`${table.td} hidden md:table-cell text-right tabular-nums`}>{u.query_count ?? 0}</td>
                <td className={`${table.td} hidden lg:table-cell text-right tabular-nums`}>{u.daily_job_quota}</td>
                <td className={`${table.td} text-right`}>
                  <span className="inline-flex flex-wrap justify-end gap-1.5">
                    <Link href={userPath(u.id)} className={btn("ghost", "sm")}>Edit</Link>
                    {u.id === me?.id ? null : (
                      <>
                        <button type="button" className={btn("ghost", "sm")} disabled={busy} onClick={() => toggleActive(u)}>
                          {u.status === "disabled" ? "Activate" : "Deactivate"}
                        </button>
                        <button type="button" className={btn("danger", "ghost", "sm")} disabled={busy} onClick={() => remove(u)}>
                          Delete
                        </button>
                      </>
                    )}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!shown.length ? <p className="m-0 px-3 py-3 text-xs text-muted">Nobody matches.</p> : null}
      </div>
    </div>
  );
}
