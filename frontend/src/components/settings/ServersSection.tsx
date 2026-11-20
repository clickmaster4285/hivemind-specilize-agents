"use client";

import Link from "next/link";

import { MODE_LABEL, SNMP_PORT } from "@/components/settings/DeviceForm";
import type { ServersApi } from "@/hooks/useServers";
import { NEW_DEVICE_PATH } from "@/lib/routes";
import type { ServerKind, ServerMode } from "@/lib/types";
import { btn, formFieldCompact, table } from "@/lib/ui";

const KIND_NAME: Record<ServerKind, string> = { ssh: "Server", nvr: "Recorder (NVR)", network: "Router / switch" };

// the dot before a device's name: what the agent may do there (recorders, routers and switches are read-only)
const modeDot: Record<ServerMode, string> = {
  audit: "bg-teal",
  confirm: "bg-copper",
  trusted: "bg-danger",
};

/** The /devices page body: the saved device logins and what the agent may do on each. Adding one is its own
 *  page, /devices/new (DeviceForm, BACKLOG L19b D5). */
export default function ServersSection({ servers, isAdmin }: { servers: ServersApi; isAdmin: boolean }) {
  return (
    <>
      <p className="text-xs leading-relaxed text-muted">
        Devices the IT specialist can log in to: servers it can work on, camera recorders (NVR/DVR) whose cameras it
        can list, and routers and switches (MikroTik and Cisco over SSH, other managed switches over SNMP) it can
        read. Each login is saved encrypted, never shown in the chat, and kept until you remove it; each person sees
        only the devices they added. For a server, the mode sets what the agent may do:{" "}
        <strong>Read-only</strong> (investigate only), <strong>ask me first</strong> (each change needs your click),
        or <strong>trusted</strong> (changes run automatically; the worst are always blocked — admins only).
        Recorders, routers and switches are always read-only.
      </p>

      {/* Saved logins */}
      {servers.servers.length ? (
        <div className={table.wrap}>
          <table className={table.table}>
            <thead className={table.head}>
              <tr>
                <th className={table.th}>Name</th>
                <th className={`${table.th} hidden sm:table-cell`}>Type</th>
                <th className={table.th}>Address</th>
                <th className={`${table.th} hidden md:table-cell`}>Login</th>
                <th className={`${table.th} hidden lg:table-cell`}>System</th>
                <th className={table.th}>Agent may</th>
                <th className={table.th}>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {servers.servers.map((s) => {
                const snmp = s.kind === "network" && s.port === SNMP_PORT;
                return (
                  <tr key={s.id} className={table.row}>
                    <td className={table.td}>
                      <span className="flex items-center gap-2 font-semibold">
                        <span className={`h-2 w-2 shrink-0 rounded-full ${modeDot[s.mode]}`} title={MODE_LABEL[s.mode]} />
                        {s.label || s.host}
                      </span>
                      {/* the type, hidden as a column on a phone */}
                      <small className="block text-xs text-muted sm:hidden">{KIND_NAME[s.kind]}</small>
                    </td>
                    <td className={`${table.td} hidden sm:table-cell whitespace-nowrap`}>{KIND_NAME[s.kind]}</td>
                    <td className={`${table.td} whitespace-nowrap font-[var(--font-mono)] text-[0.8rem]`}>
                      {s.host}:{s.port}
                    </td>
                    <td className={`${table.td} hidden md:table-cell whitespace-nowrap text-muted`}>
                      {snmp ? "SNMP community" : `${s.username} · ${s.kind === "ssh" ? s.auth : "password"}`}
                    </td>
                    <td className={`${table.td} hidden lg:table-cell text-muted`}>{s.os || "—"}</td>
                    <td className={table.td}>
                      {s.kind === "ssh" ? (
                        <select
                          className={formFieldCompact}
                          value={s.mode}
                          onChange={(e) => void servers.setMode(s.id, e.target.value as ServerMode)}
                          aria-label={`What the agent may do on ${s.label || s.host}`}
                        >
                          <option value="audit">{MODE_LABEL.audit}</option>
                          <option value="confirm">{MODE_LABEL.confirm}</option>
                          {isAdmin ? <option value="trusted">{MODE_LABEL.trusted}</option> : null}
                        </select>
                      ) : (
                        <span className="whitespace-nowrap text-muted">Read only</span>
                      )}
                    </td>
                    <td className={`${table.td} text-right`}>
                      <button
                        type="button"
                        className={btn("ghost", "sm")}
                        onClick={() => {
                          if (confirm(`Remove ${s.label || s.host}? Its saved login is deleted.`)) void servers.remove(s.id);
                        }}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-xs text-muted">
          No devices yet.{" "}
          <Link href={NEW_DEVICE_PATH} className="text-copper underline">
            Add one
          </Link>
          .
        </p>
      )}
    </>
  );
}
