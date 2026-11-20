"use client";

import { useState, type FormEvent } from "react";

import { errorText } from "@/lib/chat";
import type { ServerInfo, ServerKind, ServerLoginInput, ServerMode } from "@/lib/types";
import { btn, formFieldCompact } from "@/lib/ui";

// the usual port of each kind: SSH (servers, routers, switches), and the recorder's web API
const DEFAULT_PORT: Record<ServerKind, number> = { ssh: 22, nvr: 80, network: 22 };
// a router / switch saved on SNMP's port is read over SNMP; its password is the community string (netdev.uses_snmp)
export const SNMP_PORT = 161;
const SNMP_USER = "snmp";      // the login needs a user name; SNMPv2c has none

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

export const MODE_LABEL: Record<string, string> = {
  audit: "Read-only (audit)",
  confirm: "Operate — ask me first",
  trusted: "Operate — trusted (no asking)",
};

// Inline Tailwind building blocks (styling stays with the component).
const label = "flex flex-col gap-1 text-xs text-muted";

type Props = {
  /** adds the device, or saves the edit of `device` */
  save: (payload: ServerLoginInput) => Promise<unknown>;
  /** the saved device being edited; absent = adding a new one */
  device?: ServerInfo;
  isAdmin: boolean;
  /** the login worked and the device is saved */
  onSaved: () => void;
  onCancel: () => void;
};

function formFrom(device: ServerInfo | undefined): ServerLoginInput {
  if (!device) return emptyServer;
  // the secret is never sent to the page: its fields start empty and, left empty, keep the saved one
  return { ...emptyServer, kind: device.kind, label: device.label, host: device.host, port: device.port,
           username: device.username, mode: device.mode };
}

/** /devices/new and /devices/<id> (BACKLOG L19b D5): add or edit a server, recorder, router or switch. A new or
 *  changed login is tested before it is saved; a failed test keeps the form with its error so the user can fix
 *  it. Editing: the type stays, and a blank password / key keeps the saved one. */
export default function DeviceForm({ save, device, isAdmin, onSaved, onCancel }: Props) {
  const editing = device !== undefined;
  const [form, setForm] = useState<ServerLoginInput>(() => formFrom(device));
  const [auth, setAuth] = useState<"password" | "key">(device?.auth === "key" ? "key" : "password");
  // a router / switch: its command line or SNMP (a saved one on SNMP's port is an SNMP switch)
  const [via, setVia] = useState<"ssh" | "snmp">(device?.kind === "network" && device.port === SNMP_PORT ? "snmp" : "ssh");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (patch: Partial<ServerLoginInput>) => setForm((f) => ({ ...f, ...patch }));
  const recorder = form.kind === "nvr";          // a recorder: password only
  const snmp = form.kind === "network" && via === "snmp";   // a switch read over SNMP: a community string only
  const readOnly = form.kind !== "ssh";          // recorders, routers and switches: never changed by the agent

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await save({
        kind: form.kind,
        label: form.label,
        host: form.host,
        port: snmp ? SNMP_PORT : Number(form.port) || DEFAULT_PORT[form.kind],
        username: snmp ? SNMP_USER : form.username,
        mode: form.mode,
        ...(auth === "password" || recorder || snmp
          ? { password: form.password }
          : { private_key: form.private_key, key_passphrase: form.key_passphrase }),
      });
      setForm(emptyServer); // the password / key leaves the page's memory
      onSaved();
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <form className="flex flex-col gap-3" onSubmit={submit} autoComplete="off">
      <label className={label}>
        Type
        <select
          className={formFieldCompact}
          value={form.kind}
          disabled={editing}
          title={editing ? "A device's type cannot change — remove it and add it again" : undefined}
          onChange={(e) => {
            const kind = e.target.value as ServerKind;
            setVia("ssh");
            set({ kind, port: DEFAULT_PORT[kind] });
          }}
        >
          <option value="ssh">Server (SSH)</option>
          <option value="nvr">Camera recorder (NVR / DVR — Hikvision, Dahua)</option>
          <option value="network">Router / switch (MikroTik, Cisco, TP-Link, D-Link, Netgear, Hikvision…)</option>
        </select>
      </label>
      {form.kind === "network" ? (
        <label className={label}>
          Read with
          <select
            className={formFieldCompact}
            value={via}
            onChange={(e) => {
              const next = e.target.value as "ssh" | "snmp";
              setVia(next);
              set({ port: next === "snmp" ? SNMP_PORT : DEFAULT_PORT.network });
            }}
          >
            <option value="ssh">SSH — MikroTik, Cisco</option>
            <option value="snmp">SNMP — TP-Link, D-Link, Netgear, Hikvision and other managed switches</option>
          </select>
        </label>
      ) : null}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_1fr_5rem]">
        <label className={label}>
          Name
          <input
            className={formFieldCompact}
            placeholder="web01"
            value={form.label}
            onChange={(e) => set({ label: e.target.value })}
          />
        </label>
        <label className={label}>
          Address
          <input
            className={formFieldCompact}
            required
            placeholder="192.168.1.20 or myvps.example.com"
            value={form.host}
            onChange={(e) => set({ host: e.target.value })}
          />
        </label>
        <label className={label}>
          Port
          <input
            className={formFieldCompact}
            type="number"
            min={1}
            max={65535}
            disabled={snmp}
            value={snmp ? SNMP_PORT : form.port}
            onChange={(e) => set({ port: Number(e.target.value) })}
          />
        </label>
      </div>

      {snmp ? null : (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className={label}>
          User
          <input
            className={formFieldCompact}
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
            <select className={formFieldCompact} value={auth} onChange={(e) => setAuth(e.target.value as "password" | "key")}>
              <option value="password">Password</option>
              <option value="key">Private key</option>
            </select>
          </label>
        )}
      </div>
      )}

      {readOnly ? null : (
        <label className={label}>
          What the agent may do
          <select className={formFieldCompact} value={form.mode} onChange={(e) => set({ mode: e.target.value as ServerMode })}>
            <option value="audit">{MODE_LABEL.audit}</option>
            <option value="confirm">{MODE_LABEL.confirm}</option>
            {isAdmin ? <option value="trusted">{MODE_LABEL.trusted}</option> : null}
          </select>
        </label>
      )}

      {auth === "password" || recorder || snmp ? (
        <label className={label}>
          {snmp ? "Community string (the switch's SNMP read community)" : "Password"}
          <input
            className={formFieldCompact}
            required={!editing}
            type="password"
            placeholder={editing ? "Leave empty to keep the saved one" : snmp ? "public" : "••••••••"}
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
              className={`${formFieldCompact} font-[var(--font-mono)] resize-y`}
              required={!editing}
              rows={4}
              placeholder={editing ? "Leave empty to keep the saved key" : "-----BEGIN OPENSSH PRIVATE KEY-----"}
              spellCheck={false}
              value={form.private_key || ""}
              onChange={(e) => set({ private_key: e.target.value })}
            />
          </label>
          <label className={label}>
            Key passphrase (if any)
            <input
              className={formFieldCompact}
              type="password"
              placeholder="••••••••"
              autoComplete="new-password"
              value={form.key_passphrase || ""}
              onChange={(e) => set({ key_passphrase: e.target.value })}
            />
          </label>
        </>
      )}

      {error ? <p className="text-xs text-danger">{error}</p> : null}
      <div className="flex justify-end gap-2">
        <button type="button" className={btn("ghost", "sm")} onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="submit" className={btn("primary", "sm")} disabled={busy}>
          {busy ? (editing ? "Saving…" : "Testing the login…") : editing ? "Save" : "Add & test login"}
        </button>
      </div>
    </form>
  );
}
