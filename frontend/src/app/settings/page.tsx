"use client";

import { Suspense } from "react";

import { useApp } from "@/components/app/AppProvider";
import SettingsBody from "@/components/settings/SettingsBody";

/** The /settings page. The sidebar and navbar come from the shared shell; this is just the content. SettingsBody
 *  reads its tab from the address (?tab=), which Next wants inside a Suspense boundary. */
export default function SettingsPage() {
  const app = useApp();
  if (!app.auth.user) return null; // the shell shows the login when signed out
  return (
    <Suspense fallback={null}>
      <SettingsBody user={app.auth.user} specialists={app.specialists} />
    </Suspense>
  );
}
