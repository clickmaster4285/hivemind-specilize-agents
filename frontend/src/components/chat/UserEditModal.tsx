"use client";

import { useState, type FormEvent } from "react";

import { errorText } from "@/lib/chat";
import type { ManagedUser, UserUpdateInput } from "@/lib/types";

type Props = {
  user: ManagedUser;
  onSave: (id: number, patch: UserUpdateInput) => Promise<void>;
  onDeactivate: (id: number) => Promise<void>;
  onClose: () => void;
};

const field =
  "w-full rounded-[0.55rem] border border-[var(--line)] bg-[var(--panel-2)] px-[0.7rem] py-[0.55rem] font-[inherit] text-[var(--text)] outline-none";
const labelCol = "flex flex-col gap-1 text-[0.82rem] text-[var(--muted)]";

function fmt(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

/** Edit one account: role, team, contact, quota, status, and an optional password reset. "Deactivate"
 *  blocks sign-in but keeps the person's chats/queries. */
export default function UserEditModal({ user, onSave, onDeactivate, onClose }: Props) {
  const [role, setRole] = useState<"member" | "admin">(user.role === "admin" ? "admin" : "member");
  const [team, setTeam] = useState(user.team ?? "");
  const [email, setEmail] = useState(user.email ?? "");
  const [phone, setPhone] = useState(user.phone ?? "");
  const [quota, setQuota] = useState(String(user.daily_job_quota));
  const [status, setStatus] = useState(user.status);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const patch: UserUpdateInput = {
        role,
        team: team.trim(),
        email: email.trim(),
        phone: phone.trim(),
        daily_job_quota: Number(quota) || 0,
        status,
      };
      if (password) patch.password = password;
      await onSave(user.id, patch);
      onClose();
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  async function deactivate() {
    if (!confirm(`Deactivate ${user.username}? They can no longer sign in, but their chats and queries are kept.`)) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onDeactivate(user.id);
      onClose();
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-[rgba(6,8,12,0.72)] p-4 backdrop-blur-[4px]"
      onClick={onClose}
    >
      <form
        className="flex w-[min(560px,100%)] flex-col gap-3 rounded-2xl border border-[var(--line)] bg-[var(--panel)] px-[1.1rem] pt-4 pb-[1.1rem] shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
        onSubmit={save}
      >
        <header className="flex items-center justify-between gap-3">
          <h3 className="m-0 text-[1.15rem] [font-family:var(--font-display)]">{user.username}</h3>
          <button type="button" className="icon-btn" onClick={onClose}>
            ×
          </button>
        </header>

        {/* Read-only facts */}
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 rounded-[0.65rem] border border-[var(--line)] px-3 py-2 text-xs text-[var(--muted)]">
          <span>Questions asked: <strong className="text-[var(--text)]">{user.query_count ?? 0}</strong></span>
          <span>Created: {fmt(user.created_at)}</span>
          <span>Last sign-in: {fmt(user.last_login_at)}</span>
          <span>Last edited: {fmt(user.updated_at)}</span>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className={labelCol}>
            Role
            <select className={field} value={role} onChange={(e) => setRole(e.target.value as "member" | "admin")}>
              <option value="member">Member</option>
              <option value="admin">Admin</option>
            </select>
          </label>
          <label className={labelCol}>
            Status
            <select className={field} value={status} onChange={(e) => setStatus(e.target.value as ManagedUser["status"])}>
              <option value="active">Active</option>
              <option value="disabled">Disabled</option>
            </select>
          </label>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className={labelCol}>
            Team
            <input className={field} list="team-suggestions-edit" value={team} onChange={(e) => setTeam(e.target.value)} />
            <datalist id="team-suggestions-edit">
              <option value="IT" />
              <option value="development" />
              <option value="marketing" />
            </datalist>
          </label>
          <label className={labelCol}>
            Daily job quota
            <input className={field} type="number" min={0} value={quota} onChange={(e) => setQuota(e.target.value)} />
          </label>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className={labelCol}>
            Email
            <input className={field} type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label className={labelCol}>
            Phone
            <input className={field} type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} />
          </label>
        </div>

        <label className={labelCol}>
          Reset password (leave blank to keep current)
          <input
            className={field}
            type="password"
            autoComplete="new-password"
            placeholder="new password, min 8 characters"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>

        {error ? <p className="m-0 text-xs text-[var(--danger)]">{error}</p> : null}

        <div className="mt-1 flex items-center justify-between gap-3">
          <button type="button" className="btn ghost sm" onClick={deactivate} disabled={busy || user.status === "disabled"}>
            {user.status === "disabled" ? "Already disabled" : "Deactivate"}
          </button>
          <div className="flex gap-2">
            <button type="button" className="btn ghost sm" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className="btn primary sm" disabled={busy}>
              {busy ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
