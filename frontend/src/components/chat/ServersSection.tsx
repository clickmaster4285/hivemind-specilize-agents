"use client";

import { useState, type FormEvent } from "react";

import type { ServersApi } from "@/hooks/useServers";
import { errorText } from "@/lib/chat";
import type { ServerKind, ServerLoginInput, ServerMode } from "@/lib/types";

// the usual port of each kind: SSH (servers, routers, switches), and the recorder's web API
const DEFAULT_PORT: Record<ServerKind, number> = { ssh: 22, nvr: 80, network: 22 };
// what the list shows before the login, for the kinds that are not servers
const KIND_TAG: Record<ServerKind, string> = { ssh: "", nvr: "NVR · ", network: "Router / switch · " };

const emptyServer: ServerLoginInput = {
  kind: "ssh",
  label: "",
  host: "",
  port: DEFAULT_PORT.ssh,
  username: "",
  password: "",
  private_key: "",
  key_passphrase: "",
  mode: "audit",
};

const MODE_LABEL: Record<string, string> = {
  audit: "Read-only (audit)",
  confirm: "Operate — ask me first",
  trusted: "Operate — trusted (no asking)",
};

// Inline Tailwind building blocks (styling stays with the component).
const field =
  "w-full rounded-lg border border-[var(--line)] bg-[var(--panel-2)] px-3 py-2 text-sm text-[var(--text)] placeholder:text-[var(--muted)] outline-none focus:border-[var(--copper)]";
const label = "flex flex-col gap-1 text-xs text-[var(--muted)]";
const modeAccent: Record<ServerMode, string> = {
  audit: "border-l-[var(--teal)]",
  confirm: "border-l-[var(--copper)]",
  trusted: "border-l-[var(--danger)]",
};

/** Settings → Devices: Linux logins kept in the API's memory for this session only (admins). */
export default function ServersSection({ servers, isAdmin }: { servers: ServersApi; isAdmin: boolean }) {
  const [form, setForm] = useState<ServerLoginInput>(emptyServer);
  const [auth, setAuth] = useState<"password" | "key">("password");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (patch: Partial<ServerLoginInput>) => setForm((f) => ({ ...f, ...patch }));
  const recorder = form.kind === "nvr";          // a recorder: password only
  const readOnly = form.kind !== "ssh";          // recorders, routers and switches: never changed by the agent

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await servers.add({
        kind: form.kind,
        label: form.label,
        host: form.host,
        port: Number(form.port) || DEFAULT_PORT[form.kind],
        username: form.username,
        mode: form.mode,
        ...(auth === "password" || recorder
          ? { password: form.password }
          : { private_key: form.private_key, key_passphrase: form.key_passphrase }),
      });
      setForm({ ...emptyServer, kind: form.kind, port: DEFAULT_PORT[form.kind] }); // the password / key leaves the page's memory
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h4 className="text-base font-semibold [font-family:var(--font-display)]">Devices (this session)</h4>
      <p className="text-xs leading-relaxed text-[var(--muted)]">
        Devices the IT specialist can log in to: servers it can work on, and camera recorders (NVR/DVR) whose
        cameras it can list, and routers and switches (MikroTik) it can read. The login stays in the API&rsquo;s memory only — never
        saved, never in the chat — and is forgotten at logout, restart or after 8 hours. For a server, the mode sets
        what the agent may do: <strong>Read-only</strong>{" "}
        (investigate only), <strong>ask me first</strong> (each change needs your click), or{" "}
        <strong>trusted</strong> (changes run automatically; the worst are always blocked — admins only). Each person
        sees only the devices they added. Recorders, routers and switches are always read-only.
      </p>

      {/* Existing logins */}
      {servers.servers.length ? (
        <ul className="flex flex-col gap-2">
          {servers.servers.map((s) => (
            <li
              key={s.id}
              className={`rounded-lg border border-l-4 border-[var(--line)] ${modeAccent[s.mode]} bg-[var(--panel-2)] px-3 py-2.5 flex flex-col gap-1.5`}
            >
              <div className="flex items-center justify-between gap-2">
                <strong className="truncate text-sm">{s.label}</strong>
                <button type="button" className="btn ghost sm" onClick={() => void servers.remove(s.id)}>
                  Remove
                </button>
              </div>
              <div className="truncate text-xs text-[var(--muted)]">
                {KIND_TAG[s.kind]}
                {s.username}@{s.host}:{s.port}
                {s.kind === "ssh" ? ` · ${s.auth}` : " · read-only"}
                {s.os ? ` · ${s.os}` : ""}
              </div>
              {s.kind !== "ssh" ? null : (
              <label className="flex items-center gap-2 text-xs text-[var(--muted)]">
                <span className="shrink-0">Agent may</span>
                <select
                  className={field}
                  value={s.mode}
                  onChange={(e) => void servers.setMode(s.id, e.target.value as ServerMode)}
                  title="What the agent may do on this server"
                >
                  <option value="audit">{MODE_LABEL.audit}</option>
                  <option value="confirm">{MODE_LABEL.confirm}</option>
                  {isAdmin ? <option value="trusted">{MODE_LABEL.trusted}</option> : null}
                </select>
              </label>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-[var(--muted)]">No devices added this session.</p>
      )}

      {/* Add a login */}
      <form className="flex flex-col gap-3 border-t border-[var(--line)] pt-4" onSubmit={submit} autoComplete="off">
        <label className={label}>
          Type
          <select
            className={field}
            value={form.kind}
            onChange={(e) => {
              const kind = e.target.value as ServerKind;
              set({ kind, port: DEFAULT_PORT[kind] });
            }}
          >
            <option value="ssh">Server (SSH)</option>
            <option value="nvr">Camera recorder (NVR / DVR — Hikvision, Dahua)</option>
            <option value="network">Router / switch (MikroTik)</option>
          </select>
        </label>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_1fr_5rem]">
          <label className={label}>
            Name
            <input
              className={field}
              placeholder="web01"
              value={form.label}
              onChange={(e) => set({ label: e.target.value })}
            />
          </label>
          <label className={label}>
            Address
            <input
              className={field}
              required
              placeholder="192.168.1.20 or myvps.example.com"
              value={form.host}
              onChange={(e) => set({ host: e.target.value })}
            />
          </label>
          <label className={label}>
            Port
            <input
              className={field}
              type="number"
              min={1}
              max={65535}
              value={form.port}
              onChange={(e) => set({ port: Number(e.target.value) })}
            />
          </label>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className={label}>
            User
            <input
              className={field}
              required
              placeholder={recorder ? "admin" : "root"}
              autoComplete="off"
              value={form.username}
              onChange={(e) => set({ username: e.target.value })}
            />
          </label>
          {recorder ? null : (
            <label className={label}>
              Authentication
              <select className={field} value={auth} onChange={(e) => setAuth(e.target.value as "password" | "key")}>
                <option value="password">Password</option>
                <option value="key">Private key</option>
              </select>
            </label>
          )}
        </div>

        {readOnly ? null : (
          <label className={label}>
            What the agent may do
            <select className={field} value={form.mode} onChange={(e) => set({ mode: e.target.value as ServerMode })}>
              <option value="audit">{MODE_LABEL.audit}</option>
              <option value="confirm">{MODE_LABEL.confirm}</option>
              {isAdmin ? <option value="trusted">{MODE_LABEL.trusted}</option> : null}
            </select>
          </label>
        )}

        {auth === "password" || recorder ? (
          <label className={label}>
            Password
            <input
              className={field}
              required
              type="password"
              placeholder="••••••••"
              autoComplete="new-password"
              value={form.password || ""}
              onChange={(e) => set({ password: e.target.value })}
            />
          </label>
        ) : (
          <>
            <label className={label}>
              Private key
              <textarea
                className={`${field} font-[var(--font-mono)] resize-y`}
                required
                rows={4}
                placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
                spellCheck={false}
                value={form.private_key || ""}
                onChange={(e) => set({ private_key: e.target.value })}
              />
            </label>
            <label className={label}>
              Key passphrase (if any)
              <input
                className={field}
                type="password"
                placeholder="••••••••"
                autoComplete="new-password"
                value={form.key_passphrase || ""}
                onChange={(e) => set({ key_passphrase: e.target.value })}
              />
            </label>
          </>
        )}

        {error ? <p className="text-xs text-[var(--danger)]">{error}</p> : null}
        <button type="submit" className="btn primary sm" disabled={busy}>
          {busy ? "Testing the login…" : "Add & test login"}
        </button>
      </form>
    </>
  );
}
