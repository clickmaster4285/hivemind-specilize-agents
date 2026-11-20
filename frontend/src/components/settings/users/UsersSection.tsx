"use client";

import Link from "next/link";

import BlockedComputers from "@/components/settings/users/BlockedComputers";
import InvitesPanel from "@/components/settings/users/InvitesPanel";
import SecurityLogPanel from "@/components/settings/users/SecurityLogPanel";
import UsersTable from "@/components/settings/users/UsersTable";
import type { UsersApi } from "@/hooks/useUsers";
import { userPath } from "@/lib/routes";
import { btn } from "@/lib/ui";

/** Settings → Users: sign-ups waiting for approval, sign-up and invites, the accounts and a Create user
 *  button. Creating, editing or reviewing one opens its own page, /settings/users/<id> (BACKLOG L19b D5).
 *  The accounts themselves — search, filters, sorting, row and bulk actions — are UsersTable. */
export default function UsersSection({ users }: { users: UsersApi }) {
  const waiting = users.users.filter((u) => u.status === "pending");
  const accounts = users.users.filter((u) => u.status !== "pending");

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
        active at once — the person chooses their own password at the first sign-in; people who sign up themselves wait
        below for your approval. A forgotten password: open the user and make a reset link.
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

      <UsersTable users={users} accounts={accounts} />

      <SecurityLogPanel />
    </>
  );
}
