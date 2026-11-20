"use client";

import { useParams, useRouter } from "next/navigation";

import { useApp } from "@/components/app/AppProvider";
import PageHeader from "@/components/app/PageHeader";
import DeviceForm from "@/components/settings/DeviceForm";
import DeviceSharing from "@/components/settings/DeviceSharing";
import { useServers } from "@/hooks/useServers";
import { DEVICES_PATH } from "@/lib/routes";

/** /devices/<id>: edit a saved device (the user's ask, 2026-11-20). Name and mode save at once; a new address,
 *  user or password is tested first; a blank password keeps the saved one. Saving or Cancel returns to /devices. */
export default function EditDevicePage() {
  const app = useApp();
  const router = useRouter();
  const { id: raw } = useParams<{ id: string }>();
  const id = decodeURIComponent(raw);
  const user = app.auth.user;
  const canDevices = Boolean(user?.modules?.includes("devices"));
  const servers = useServers(canDevices);
  if (!user) return null; // the shell shows the login when signed out
  const back = () => router.push(DEVICES_PATH);
  const device = servers.servers.find((s) => s.id === id);

  let body;
  if (!canDevices) body = <p className="mt-6 text-[0.9rem] text-muted">Ask an admin for access to devices.</p>;
  else if (!servers.loaded) body = <p className="mt-6 text-[0.9rem] text-muted">Loading…</p>;
  else if (!device) {
    body = (
      <div className="mt-6">
        <p className="text-[0.9rem] text-muted">This device no longer exists.</p>
        <button type="button" className="mt-3 text-sm text-copper underline" onClick={back}>
          ← Devices
        </button>
      </div>
    );
  } else if (device.shared_by) {
    body = (
      <p className="mt-6 text-[0.9rem] text-muted">
        {device.label} is shared with you by {device.shared_by}: you use it from chat, and only they can change it.
      </p>
    );
  } else {
    body = (
      <>
        <section className="mt-6 rounded-xl border border-line bg-panel p-5">
          <DeviceForm
            key={device.id}
            device={device}
            save={(payload) => servers.update(device.id, payload)}
            isAdmin={user.role === "admin"}
            onSaved={back}
            onCancel={back}
          />
        </section>
        {/* an admin's own device can be shared with people or teams (BACKLOG L30 T4) */}
        {user.role === "admin" ? <DeviceSharing deviceId={device.id} /> : null}
      </>
    );
  }
  return (
    <PageHeader
      title={device ? `Edit ${device.label}` : "Edit device"}
      about="A changed address or login is tested before it is saved. Leave the password empty to keep the saved one."
    >
      {body}
    </PageHeader>
  );
}
