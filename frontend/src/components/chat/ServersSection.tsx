"use client";

import { useState, type FormEvent } from "react";

import type { ServersApi } from "@/hooks/useServers";
import { errorText } from "@/lib/chat";
import type { ServerLoginInput } from "@/lib/types";

const emptyServer: ServerLoginInput = {
  label: "",
  host: "",
  port: 22,
  username: "",
  password: "",
  private_key: "",
  key_passphrase: "",
};

/** Settings → Servers: Linux logins kept in the API's memory for this session only (admins). */
export default function ServersSection({ servers }: { servers: ServersApi }) {
  const [form, setForm] = useState<ServerLoginInput>(emptyServer);
  const [auth, setAuth] = useState<"password" | "key">("password");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (patch: Partial<ServerLoginInput>) => setForm((f) => ({ ...f, ...patch }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await servers.add({
        label: form.label,
        host: form.host,
        port: Number(form.port) || 22,
        username: form.username,
        ...(auth === "password"
          ? { password: form.password }
          : { private_key: form.private_key, key_passphrase: form.key_passphrase }),
      });
      setForm(emptyServer); // the password / key leaves the page's memory
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <div className="rail-section-head">
        <h4>Servers (this session)</h4>
      </div>
      <p className="hint">
        Linux servers the IT specialist may audit (&ldquo;audit web01&rdquo;). The login stays in the API&rsquo;s memory
        only — never saved, never in the chat — and is forgotten at logout, restart or after 8 hours. Read-only: nothing
        on the server is changed.
      </p>
      {servers.servers.length ? (
        <ul className="settings-agents">
          {servers.servers.map((s) => (
            <li key={s.id}>
              <span className="specialist-row">
                <span>
                  <strong>{s.label}</strong>
                  <small>
                    {s.username}@{s.host}:{s.port} · {s.auth} · key {s.host_key.split(" ").pop()?.slice(0, 18)}…
                  </small>
                </span>
              </span>
              <button type="button" className="btn ghost sm" onClick={() => void servers.remove(s.id)}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <form className="server-form" onSubmit={submit} autoComplete="off">
        <div className="server-form-row">
          <input placeholder="Name (e.g. web01)" value={form.label} onChange={(e) => set({ label: e.target.value })} />
          <input
            required
            placeholder="Address (192.168.1.20)"
            value={form.host}
            onChange={(e) => set({ host: e.target.value })}
          />
          <input
            type="number"
            min={1}
            max={65535}
            className="server-port"
            value={form.port}
            onChange={(e) => set({ port: Number(e.target.value) })}
          />
        </div>
        <div className="server-form-row">
          <input
            required
            placeholder="User"
            autoComplete="off"
            value={form.username}
            onChange={(e) => set({ username: e.target.value })}
          />
          <select value={auth} onChange={(e) => setAuth(e.target.value as "password" | "key")}>
            <option value="password">Password</option>
            <option value="key">Private key</option>
          </select>
        </div>
        {auth === "password" ? (
          <input
            required
            type="password"
            placeholder="Password"
            autoComplete="new-password"
            value={form.password || ""}
            onChange={(e) => set({ password: e.target.value })}
          />
        ) : (
          <>
            <textarea
              required
              rows={4}
              className="server-key"
              placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
              spellCheck={false}
              value={form.private_key || ""}
              onChange={(e) => set({ private_key: e.target.value })}
            />
            <input
              type="password"
              placeholder="Key passphrase (if any)"
              autoComplete="new-password"
              value={form.key_passphrase || ""}
              onChange={(e) => set({ key_passphrase: e.target.value })}
            />
          </>
        )}
        {error ? <p className="server-error">{error}</p> : null}
        <button type="submit" className="btn primary sm" disabled={busy}>
          {busy ? "Testing the login…" : "Add & test login"}
        </button>
      </form>
    </section>
  );
}
