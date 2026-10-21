"use client";

import { useApp } from "@/components/app/AppProvider";
import SettingsBody from "@/components/chat/SettingsBody";

/** The /settings page. The sidebar and navbar come from the shared shell; this is just the content. */
export default function SettingsPage() {
  const app = useApp();
  if (!app.auth.user) return null; // the shell shows the login when signed out
  return <SettingsBody user={app.auth.user} specialists={app.specialists} models={app.models} />;
}
