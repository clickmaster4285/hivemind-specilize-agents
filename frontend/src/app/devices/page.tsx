"use client";

import { useApp } from "@/components/app/AppProvider";
import PageHeader from "@/components/app/PageHeader";
import ServersSection from "@/components/settings/ServersSection";
import { useServers } from "@/hooks/useServers";

/** /devices (BACKLOG L24 U5): the servers, routers, switches and recorders the assistant may log in to —
 *  moved here from a Settings tab. Shown to users an admin gave the Devices module. */
export default function DevicesPage() {
  const app = useApp();
  const user = app.auth.user;
  const canDevices = Boolean(user?.modules?.includes("devices"));
  const servers = useServers(canDevices);
  if (!user) return null; // the shell shows the login when signed out
  return (
    <PageHeader title="Devices" about="Servers, routers, switches and recorders the assistant can log in to.">
      {canDevices ? (
        <section className="mt-6 flex flex-col gap-3 rounded-xl border border-line bg-panel p-5">
          <ServersSection servers={servers} isAdmin={user.role === "admin"} />
        </section>
      ) : (
        <p className="mt-6 text-[0.9rem] text-muted">Ask an admin for access to devices.</p>
      )}
    </PageHeader>
  );
}
