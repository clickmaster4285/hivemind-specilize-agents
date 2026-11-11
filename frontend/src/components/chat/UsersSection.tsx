"use client";

import { useState } from "react";

import InvitesPanel from "@/components/chat/InvitesPanel";
import UserForm from "@/components/chat/UserForm";
import type { UsersApi } from "@/hooks/useUsers";
import type { ManagedUser } from "@/lib/types";

/** Settings → Users: sign-ups waiting for approval, sign-up and invites, the accounts and a Create user
 *  button. Creating, editing or reviewing one opens the full-page UserForm in place of the list. */
export default function UsersSection({ users }: { users: UsersApi }) {
  // undefined = the list, null = a new user, a user = editing them
  const [open, setOpen] = useState<ManagedUser | null | undefined>(undefined);

  if (open !== undefined) {
    return <UserForm users={users} user={open} onDone={() => setOpen(undefined)} />;
  }
  const waiting = users.users.filter((u) => u.status === "pending");
  const accounts = users.users.filter((u) => u.status !== "pending");

  return (
    <>
      <div className="flex items-center justify-between gap-3">
        <h4 className="text-base font-semibold [font-family:var(--font-display)]">Users</h4>
        <button type="button" className="btn primary sm" onClick={() => setOpen(null)}>
          Create user
        </button>
      </div>
      <p className="text-xs leading-relaxed text-[var(--muted)]">
        Give each person their own login so their questions are attributed to them, and choose which modules they
        may use. Set a <strong>team</strong> (e.g. IT, marketing) to group what people search. Accounts made here are
        active at once; people who sign up themselves wait below for your approval. Passwords are stored hashed and
        never shown again; share the first password with the person directly.
      </p>

      {waiting.length ? (
        <section className="flex flex-col gap-2">
          <h5 className="m-0 flex items-center gap-2 text-sm font-semibold">
            Waiting for approval
            <span className="rounded-full bg-[var(--copper)] px-1.5 py-0.5 text-[0.65rem] text-[var(--on-copper)]">
              {waiting.length}
            </span>
          </h5>
          <ul className="flex flex-col gap-2">
            {waiting.map((u) => (
              <li
                key={u.id}
                className="flex items-center justify-between gap-3 rounded-lg border border-[var(--copper)] bg-[var(--panel-2)] px-3 py-2.5"
              >
                <span className="min-w-0">
                  <strong className="block truncate text-sm">{u.username}</strong>
                  <small className="block truncate text-xs text-[var(--muted)]">
                    {u.email}
                    {u.phone ? ` · ${u.phone}` : ""}
                    {u.invited_by ? ` · invited by ${u.invited_by}` : ""}
                    {u.signup_address ? ` · from ${u.signup_address}` : ""}
                  </small>
                </span>
                <button type="button" className="btn primary sm shrink-0" onClick={() => setOpen(u)}>
                  Review
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <InvitesPanel />

      {accounts.length ? (
        <ul className="flex flex-col gap-2">
          {accounts.map((u) => (
            <li
              key={u.id}
              className={`flex items-center justify-between gap-3 rounded-lg border border-[var(--line)] bg-[var(--panel-2)] px-3 py-2.5 ${
                u.status === "disabled" ? "opacity-60" : ""
              }`}
            >
              <span className="min-w-0">
                <strong className="flex items-center gap-2 truncate text-sm">
                  {u.username}
                  {u.status === "disabled" ? (
                    <span className="rounded-full border border-[var(--line)] px-1.5 py-0.5 text-[0.65rem] font-normal text-[var(--muted)]">
                      disabled
                    </span>
                  ) : null}
                </strong>
                <small className="block truncate text-xs text-[var(--muted)]">
                  {u.role}
                  {u.team ? ` · ${u.team}` : ""} · quota {u.daily_job_quota} · {u.query_count ?? 0} queries
                </small>
                <small className="block truncate text-xs text-[var(--muted)]">
                  Modules: {u.modules.length ? u.modules.join(", ") : "chat only"}
                </small>
              </span>
              <button type="button" className="btn ghost sm shrink-0" onClick={() => setOpen(u)}>
                Edit
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-[var(--muted)]">No accounts yet.</p>
      )}
    </>
  );
}
