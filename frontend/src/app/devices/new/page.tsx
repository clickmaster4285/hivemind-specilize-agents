"use client";

import { useRouter } from "next/navigation";

import { useApp } from "@/components/app/AppProvider";
import PageHeader from "@/components/app/PageHeader";
import DeviceForm from "@/components/settings/DeviceForm";
import { useServers } from "@/hooks/useServers";
import { DEVICES_PATH } from "@/lib/routes";

/** /devices/new (BACKLOG L19b D5): the add-device form as its own page. A working login returns to /devices,
 *  where the new device is listed; Cancel returns without saving. */
export default function NewDevicePage() {
  const app = useApp();
  const router = useRouter();
  const user = app.auth.user;
  const canDevices = Boolean(user?.modules?.includes("devices"));
  const servers = useServers(false);   // only adds here; the list loads on /devices
  if (!user) return null; // the shell shows the login when signed out
  const back = () => router.push(DEVICES_PATH);
  return (
    <PageHeader title="Add device" about="The login is tested before it is saved, encrypted, for you only.">
      {canDevices ? (
        <section className="mt-6 rounded-xl border border-line bg-panel p-5">
          <DeviceForm save={servers.add} isAdmin={user.role === "admin"} onSaved={back} onCancel={back} />
        </section>
      ) : (
        <p className="mt-6 text-[0.9rem] text-muted">Ask an admin for access to devices.</p>
      )}
    </PageHeader>
  );
}
