"use client";

import Link from "next/link";
import { useState } from "react";

import { useApp } from "@/components/app/AppProvider";
import BlockedComputers from "@/components/settings/users/BlockedComputers";
import CommandLogPanel from "@/components/settings/users/CommandLogPanel";
import InvitesPanel from "@/components/settings/users/InvitesPanel";
import SecurityLogPanel from "@/components/settings/users/SecurityLogPanel";
import type { UsersApi } from "@/hooks/useUsers";
import { errorText } from "@/lib/chat";
import { userPath } from "@/lib/routes";
import type { ManagedUser } from "@/lib/types";
import { btn, table } from "@/lib/ui";

/** Settings → Users: sign-ups waiting for approval, sign-up and invites, the accounts and a Create user
 *  button. Creating, editing or reviewing one opens its own page, /settings/users/<id> (BACKLOG L19b D5).
 *  Each account row also has Deactivate / Activate (sign-in blocked, everything kept) and Delete (for good,
 *  after typing the username) — the user's choice 2026-11-20. */
export default function UsersSection({ users }: { users: UsersApi }) {
  const me = useApp().auth.user;
  const [busy, setBusy] = useState<number | null>(null);
  const [notice, setNotice] = useState<{ text: string; bad: boolean } | null>(null);
  const waiting = users.users.filter((u) => u.status === "pending");
  const accounts = users.users.filter((u) => u.status !== "pending");

  async function act(u: ManagedUser, work: () => Promise<string>) {
    setBusy(u.id);
    setNotice(null);
    try {
      setNotice({ text: await work(), bad: false });
    } catch (err) {
      setNotice({ text: errorText(err), bad: true });
    } finally {
      setBusy(null);
    }
  }

  function toggleActive(u: ManagedUser) {
    if (u.status === "disabled") {
      void act(u, async () => {
        await users.update(u.id, { status: "active" });
        return `${u.username} can sign in again.`;
      });
      return;
    }
    if (!confirm(`Deactivate ${u.username}? They can no longer sign in; their chats and queries are kept, and you can activate them again.`)) return;
    void act(u, async () => {
      await users.deactivate(u.id);
      return `${u.username} is deactivated.`;
    });
  }

  function remove(u: ManagedUser) {
    const typed = prompt(
      `Delete ${u.username} for good?\n\nTheir account, chats, projects, schedules, devices and own skills are removed ` +
        `and cannot be brought back. To keep their history, use Deactivate instead.\n\nType the username to confirm:`,
    );
    if (typed === null) return;
    if (typed.trim() !== u.username) {
      setNotice({ text: "The username did not match — nothing was deleted.", bad: true });
      return;
    }
    void act(u, async () => {
      const done = await users.remove(u.id, typed.trim());
      const kb = done.knowledge_base.startsWith("not removed") ? ` Knowledge base: ${done.knowledge_base}.` : "";
      return `${done.username} was deleted with ${done.questions} questions.${kb}`;
    });
  }

  return (
    <>
      <div className="flex items-center justify-between gap-3">
        <h4 className="text-base font-semibold [font-family:var(--font-display)]">Users</h4>
        <Link href={userPath("new")} className={btn("primary", "sm")}>
          Create user
        </Link>
      </div>
      <p className="text-xs leading-relaxed text-muted">
        Give each person their own login so their questions are attributed to them, and choose which modules they
        may use. Set a <strong>team</strong> (e.g. IT, marketing) to group what people search. Accounts made here are
        active at once; people who sign up themselves wait below for your approval. Passwords are stored hashed and
        never shown again; share the first password with the person directly.
      </p>

      {waiting.length ? (
        <section className="flex flex-col gap-2">
          <h5 className="m-0 flex items-center gap-2 text-sm font-semibold">
            Waiting for approval
            <span className="rounded-full bg-copper px-1.5 py-0.5 text-[0.65rem] text-on-copper">
              {waiting.length}
            </span>
          </h5>
          <ul className="flex flex-col gap-2">
            {waiting.map((u) => (
              <li
                key={u.id}
                className="flex items-center justify-between gap-3 rounded-lg border border-copper bg-panel-2 px-3 py-2.5"
              >
                <span className="min-w-0">
                  <strong className="block truncate text-sm">{u.username}</strong>
                  <small className="block truncate text-xs text-muted">
                    {u.email}
                    {u.phone ? ` · ${u.phone}` : ""}
                    {u.invited_by ? ` · invited by ${u.invited_by}` : ""}
                    {u.signup_address ? ` · from ${u.signup_address}` : ""}
                  </small>
                </span>
                <Link href={userPath(u.id)} className={`${btn("primary", "sm")} shrink-0`}>
                  Review
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <InvitesPanel />
      <BlockedComputers />

      {notice ? (
        <p role="status" className={`m-0 text-xs ${notice.bad ? "text-danger" : "text-muted"}`}>
          {notice.text}
        </p>
      ) : null}
      {accounts.length ? (
        <div className={table.wrap}>
          <table className={table.table}>
            <thead className={table.head}>
              <tr>
                <th className={table.th}>User</th>
                <th className={`${table.th} hidden sm:table-cell`}>Role</th>
                <th className={`${table.th} hidden md:table-cell`}>Team</th>
                <th className={`${table.th} hidden lg:table-cell`}>Modules</th>
                <th className={`${table.th} hidden md:table-cell text-right`}>Queries</th>
                <th className={`${table.th} hidden lg:table-cell text-right`}>Daily quota</th>
                <th className={table.th}>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((u) => (
                <tr key={u.id} className={`${table.row} ${u.status === "disabled" ? "opacity-60" : ""}`}>
                  <td className={table.td}>
                    <span className="flex items-center gap-2 font-semibold">
                      {u.username}
                      {u.status === "disabled" ? (
                        <span className="rounded-full border border-line px-1.5 py-0.5 text-[0.65rem] font-normal text-muted">
                          disabled
                        </span>
                      ) : null}
                      {u.locked_until ? (
                        <span
                          className="rounded-full border border-danger px-1.5 py-0.5 text-[0.65rem] font-normal text-danger"
                          title={`${u.failed_logins} failed passwords — waits until ${new Date(u.locked_until).toLocaleTimeString()}`}
                        >
                          locked
                        </span>
                      ) : null}
                    </span>
                    {u.email ? <small className="block text-xs text-muted">{u.email}</small> : null}
                    {/* the columns hidden on a small screen, under the name instead */}
                    <small className="block text-xs text-muted md:hidden">
                      {u.role}
                      {u.team ? ` · ${u.team}` : ""} · {u.query_count ?? 0} queries
                    </small>
                  </td>
                  <td className={`${table.td} hidden sm:table-cell capitalize`}>{u.role}</td>
                  <td className={`${table.td} hidden md:table-cell text-muted`}>{u.team || "—"}</td>
                  <td className={`${table.td} hidden lg:table-cell text-muted`}>{u.modules.length ? u.modules.join(", ") : "chat only"}</td>
                  <td className={`${table.td} hidden md:table-cell text-right tabular-nums`}>{u.query_count ?? 0}</td>
                  <td className={`${table.td} hidden lg:table-cell text-right tabular-nums`}>{u.daily_job_quota}</td>
                  <td className={`${table.td} text-right`}>
                    <span className="inline-flex flex-wrap justify-end gap-1.5">
                      <Link href={userPath(u.id)} className={btn("ghost", "sm")}>
                        Edit
                      </Link>
                      {u.id === me?.id ? null : (
                        <>
                          <button type="button" className={btn("ghost", "sm")} disabled={busy !== null}
                                  onClick={() => toggleActive(u)}>
                            {u.status === "disabled" ? "Activate" : "Deactivate"}
                          </button>
                          <button type="button" className={btn("danger", "ghost", "sm")} disabled={busy !== null}
                                  onClick={() => remove(u)}>
                            {busy === u.id ? "…" : "Delete"}
                          </button>
                        </>
                      )}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-xs text-muted">No accounts yet.</p>
      )}

      <SecurityLogPanel />
      <CommandLogPanel />
    </>
  );
}
