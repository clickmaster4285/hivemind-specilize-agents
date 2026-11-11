"use client";

import { useEffect, useState, type FormEvent } from "react";

import type { UsersApi } from "@/hooks/useUsers";
import { listModules } from "@/lib/api";
import { errorText } from "@/lib/chat";
import type { ManagedUser, ModuleInfo, UserStatus } from "@/lib/types";
import { btn } from "@/lib/ui";

// Inline Tailwind building blocks (styling stays with the component), matching ServersSection.
const field =
  "w-full rounded-lg border border-line bg-panel-2 px-3 py-2 text-sm text-text placeholder:text-muted outline-none focus:border-copper";
const label = "flex flex-col gap-1 text-xs text-muted";
const section = "flex flex-col gap-3 rounded-xl border border-line p-4";
const sectionTitle = "m-0 text-sm font-semibold";

function fmt(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

type Props = {
  users: UsersApi;
  /** the account to edit; null = create a new one */
  user: ManagedUser | null;
  onDone: () => void;
};

/** Settings → Users → one account, full page: the same form creates and edits. Module access says what a
 *  member may use (Devices, Skills, Usage…); admins have every module. "Deactivate" blocks sign-in but keeps
 *  the person's chats and queries. */
export default function UserForm({ users, user, onDone }: Props) {
  const creating = user === null;
  const [username, setUsername] = useState(user?.username ?? "");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"member" | "admin">(user?.role === "admin" ? "admin" : "member");
  const [status, setStatus] = useState<UserStatus>(user?.status ?? "active");
  const [team, setTeam] = useState(user?.team ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [phone, setPhone] = useState(user?.phone ?? "");
  const [quota, setQuota] = useState(user ? String(user.daily_job_quota) : "");
  const [catalog, setCatalog] = useState<ModuleInfo[]>([]);
  const [granted, setGranted] = useState<string[] | null>(user ? user.modules : null);   // null = defaults, once loaded
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listModules()
      .then((list) => {
        if (cancelled) return;
        setCatalog(list);
        setGranted((g) => g ?? list.filter((m) => m.default).map((m) => m.id));
      })
      .catch((err) => !cancelled && setError(errorText(err)));
    return () => {
      cancelled = true;
    };
  }, []);

  const admin = role === "admin";
  const modules = granted ?? [];
  const toggle = (id: string, on: boolean) =>
    setGranted((g) => (on ? [...(g ?? []), id] : (g ?? []).filter((m) => m !== id)));

  // a sign-up waiting for approval: saving keeps it waiting; Approve saves and activates it
  const pending = user?.status === "pending";

  async function save(event: FormEvent, approve = false) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const common = { role, daily_job_quota: quota ? Number(quota) : null, modules };
    try {
      if (creating) {
        await users.create({
          username: username.trim(),
          password,
          ...common,
          team: team.trim() || null,
          email: email.trim() || null,
          phone: phone.trim() || null,
        });
      } else {
        // an empty string clears team / email / phone on an existing account
        await users.update(user.id, {
          ...common,
          team: team.trim(),
          email: email.trim(),
          phone: phone.trim(),
          ...(approve ? { status: "active" as const } : status !== "pending" ? { status } : {}),
          ...(password ? { password } : {}),
        });
      }
      onDone(); // the password leaves the page's memory with the form
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  async function unlock() {
    if (!user) return;
    setBusy(true);
    setError(null);
    try {
      await users.unlock(user.id);
      onDone();
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  async function deactivate() {
    const question = pending
      ? `Reject the sign-up of ${user?.username}? The request is deleted.`
      : `Deactivate ${user?.username}? They can no longer sign in, but their chats and queries are kept.`;
    if (!user || !confirm(question)) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await users.deactivate(user.id);
      onDone();
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <form className="flex flex-col gap-4" onSubmit={save} autoComplete="off">
      <header className="flex items-center gap-3">
        <button type="button" className={btn("ghost", "sm")} onClick={onDone} disabled={busy}>
          ← Users
        </button>
        <h4 className="m-0 text-base font-semibold [font-family:var(--font-display)]">
          {creating ? "New user" : user.username}
        </h4>
      </header>
      {user?.locked_until ? (
        <p className="m-0 flex items-center justify-between gap-3 rounded-lg border border-danger px-3 py-2 text-xs">
          <span>
            Locked after {user.failed_logins} failed passwords — can sign in again at{" "}
            {new Date(user.locked_until).toLocaleTimeString()}.
          </span>
          <button type="button" className={`${btn("ghost", "sm")} shrink-0`} disabled={busy} onClick={() => void unlock()}>
            Unlock now
          </button>
        </p>
      ) : null}
      {pending ? (
        <p className="m-0 rounded-lg border border-line bg-panel-2 px-3 py-2 text-xs text-muted">
          Signed up {user.invited_by ? `with ${user.invited_by}'s invite` : "on the sign-up page"}
          {user.signup_address ? ` from ${user.signup_address}` : ""} — waiting for your approval. Check the details and
          modules, then approve or reject.
        </p>
      ) : null}

      {user ? (
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 rounded-xl border border-line px-4 py-2.5 text-xs text-muted">
          <span>
            Questions asked: <strong className="text-text">{user.query_count ?? 0}</strong>
          </span>
          <span>Created: {fmt(user.created_at)}</span>
          <span>Last sign-in: {fmt(user.last_login_at)}</span>
          <span>Last edited: {fmt(user.updated_at)}</span>
        </div>
      ) : null}

      <div className={section}>
        <h5 className={sectionTitle}>Account</h5>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className={label}>
            Username
            <input
              className={field}
              required
              minLength={3}
              placeholder="e.g. alice"
              disabled={!creating}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>
          <label className={label}>
            {creating ? "Password" : "Reset password (leave blank to keep it)"}
            <input
              className={field}
              required={creating}
              minLength={8}
              type="password"
              placeholder="at least 8 characters"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <label className={label}>
            Role
            <select className={field} value={role} onChange={(e) => setRole(e.target.value as "member" | "admin")}>
              <option value="member">Member</option>
              <option value="admin">Admin</option>
            </select>
          </label>
          {creating || pending ? null : (
            <label className={label}>
              Status
              <select className={field} value={status} onChange={(e) => setStatus(e.target.value as UserStatus)}>
                <option value="active">Active</option>
                <option value="disabled">Disabled</option>
              </select>
            </label>
          )}
        </div>
      </div>

      <div className={section}>
        <h5 className={sectionTitle}>Details</h5>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className={label}>
            Team
            <input
              className={field}
              list="team-suggestions"
              placeholder="IT, marketing… (optional)"
              value={team}
              onChange={(e) => setTeam(e.target.value)}
            />
            <datalist id="team-suggestions">
              <option value="IT" />
              <option value="development" />
              <option value="marketing" />
            </datalist>
          </label>
          <label className={label}>
            Daily job quota
            <input
              className={field}
              type="number"
              min={0}
              placeholder="default"
              value={quota}
              onChange={(e) => setQuota(e.target.value)}
            />
          </label>
          <label className={label}>
            Email
            <input
              className={field}
              type="email"
              placeholder="name@example.com (optional)"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <label className={label}>
            Phone
            <input
              className={field}
              type="tel"
              placeholder="+92 300 1234567 (optional)"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
            />
          </label>
        </div>
      </div>

      <div className={section}>
        <h5 className={sectionTitle}>Module access</h5>
        <p className="m-0 text-xs text-muted">
          {admin ? "Admins have every module." : "What this person may use besides chat."}
        </p>
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {catalog.map((m) => (
            <li key={m.id}>
              <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-line bg-panel-2 px-3 py-2.5">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={admin || modules.includes(m.id)}
                  disabled={admin}
                  onChange={(e) => toggle(m.id, e.target.checked)}
                />
                <span className="min-w-0">
                  <strong className="block text-sm">{m.title}</strong>
                  <small className="block text-xs text-muted">{m.description}</small>
                </span>
              </label>
            </li>
          ))}
        </ul>
      </div>

      {error ? <p className="m-0 text-xs text-danger">{error}</p> : null}

      <div className="flex items-center justify-between gap-3">
        {user ? (
          <button type="button" className={btn("ghost", "sm")} onClick={deactivate} disabled={busy || user.status === "disabled"}>
            {pending ? "Reject" : user.status === "disabled" ? "Already disabled" : "Deactivate"}
          </button>
        ) : (
          <span />
        )}
        <div className="flex gap-2">
          <button type="button" className={btn("ghost", "sm")} onClick={onDone} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className={btn(pending ? "ghost" : "primary", "sm")} disabled={busy}>
            {busy ? "Saving…" : creating ? "Create user" : "Save"}
          </button>
          {pending ? (
            <button type="button" className={btn("primary", "sm")} disabled={busy} onClick={(e) => void save(e, true)}>
              Approve
            </button>
          ) : null}
        </div>
      </div>
    </form>
  );
}
