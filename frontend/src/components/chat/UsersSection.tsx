"use client";

import { useState, type FormEvent } from "react";

import UserEditModal from "@/components/chat/UserEditModal";
import type { UsersApi } from "@/hooks/useUsers";
import { errorText } from "@/lib/chat";
import type { ManagedUser, UserCreateInput } from "@/lib/types";

const emptyUser: UserCreateInput = { username: "", password: "", role: "member", team: "", email: "", phone: "" };

// Inline Tailwind building blocks (styling stays with the component), matching ServersSection.
const field =
  "w-full rounded-lg border border-[var(--line)] bg-[var(--panel-2)] px-3 py-2 text-sm text-[var(--text)] placeholder:text-[var(--muted)] outline-none focus:border-[var(--copper)]";
const label = "flex flex-col gap-1 text-xs text-[var(--muted)]";

/** Settings → Users: admins create local accounts so each person signs in with their own login and
 *  their chat queries are attributed to them (and, via the team tag, to their group). */
export default function UsersSection({ users }: { users: UsersApi }) {
  const [form, setForm] = useState<UserCreateInput>(emptyUser);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [editing, setEditing] = useState<ManagedUser | null>(null);
  const set = (patch: Partial<UserCreateInput>) => setForm((f) => ({ ...f, ...patch }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      await users.create({
        username: form.username.trim(),
        password: form.password,
        role: form.role,
        team: form.team?.trim() || null,
        email: form.email?.trim() || null,
        phone: form.phone?.trim() || null,
      });
      setDone(`Created ${form.username.trim()}.`);
      setForm(emptyUser); // the password leaves the page's memory
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h4 className="text-base font-semibold [font-family:var(--font-display)]">Users</h4>
      <p className="text-xs leading-relaxed text-[var(--muted)]">
        Give each person their own login so their questions are attributed to them. Set a{" "}
        <strong>team</strong> (e.g. IT, marketing) to group what people search. Open sign-up is off — accounts are
        created here. Passwords are stored hashed and never shown again; share the first password with the person
        directly.
      </p>

      {/* Existing accounts */}
      {users.users.length ? (
        <ul className="flex flex-col gap-2">
          {users.users.map((u) => (
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
                {u.email || u.phone ? (
                  <small className="block truncate text-xs text-[var(--muted)]">
                    {[u.email, u.phone].filter(Boolean).join(" · ")}
                  </small>
                ) : null}
              </span>
              <button type="button" className="btn ghost sm shrink-0" onClick={() => setEditing(u)}>
                Edit
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-[var(--muted)]">No accounts yet.</p>
      )}

      {/* Add a user */}
      <form className="flex flex-col gap-3 border-t border-[var(--line)] pt-4" onSubmit={submit} autoComplete="off">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className={label}>
            Username
            <input
              className={field}
              required
              minLength={3}
              placeholder="e.g. alice"
              autoComplete="off"
              value={form.username}
              onChange={(e) => set({ username: e.target.value })}
            />
          </label>
          <label className={label}>
            Password
            <input
              className={field}
              required
              minLength={8}
              type="password"
              placeholder="at least 8 characters"
              autoComplete="new-password"
              value={form.password}
              onChange={(e) => set({ password: e.target.value })}
            />
          </label>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className={label}>
            Role
            <select
              className={field}
              value={form.role}
              onChange={(e) => set({ role: e.target.value as UserCreateInput["role"] })}
            >
              <option value="member">Member</option>
              <option value="admin">Admin</option>
            </select>
          </label>
          <label className={label}>
            Team
            <input
              className={field}
              list="team-suggestions"
              placeholder="IT, marketing… (optional)"
              value={form.team ?? ""}
              onChange={(e) => set({ team: e.target.value })}
            />
            <datalist id="team-suggestions">
              <option value="IT" />
              <option value="development" />
              <option value="marketing" />
            </datalist>
          </label>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className={label}>
            Email
            <input
              className={field}
              type="email"
              placeholder="name@example.com (optional)"
              autoComplete="off"
              value={form.email ?? ""}
              onChange={(e) => set({ email: e.target.value })}
            />
          </label>
          <label className={label}>
            Phone
            <input
              className={field}
              type="tel"
              placeholder="+92 300 1234567 (optional)"
              autoComplete="off"
              value={form.phone ?? ""}
              onChange={(e) => set({ phone: e.target.value })}
            />
          </label>
        </div>

        {error ? <p className="text-xs text-[var(--danger)]">{error}</p> : null}
        {done ? <p className="text-xs text-[var(--teal)]">{done}</p> : null}
        <button type="submit" className="btn primary sm" disabled={busy}>
          {busy ? "Creating…" : "Create user"}
        </button>
      </form>

      {editing ? (
        <UserEditModal
          user={editing}
          onSave={users.update}
          onDeactivate={users.deactivate}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </>
  );
}
