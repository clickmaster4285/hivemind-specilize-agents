"use client";

import { usePathname, useRouter } from "next/navigation";

import { useApp } from "@/components/app/AppProvider";
import ChatSidebar from "@/components/chat/ChatSidebar";
import type { ChatThread } from "@/lib/threads";

/** The persistent left rail. Reads shared state from the provider; opening a chat from the settings
 *  page first navigates back to the chat. */
export default function Sidebar() {
  const app = useApp();
  const router = useRouter();
  const pathname = usePathname();

  function toChat(action: () => void) {
    if (pathname !== "/") router.push("/");
    action();
  }

  return (
    <ChatSidebar
      username={app.auth.user?.username || ""}
      history={app.history}
      health={app.health.health}
      onNewChat={() => toChat(app.newChat)}
      onOpenThread={(thread: ChatThread) => toChat(() => void app.chat.openThread(thread))}
      onDeleteThread={(thread: ChatThread) => void app.deleteThread(thread)}
      onOpenSettings={() => router.push("/settings")}
      onSignOut={app.auth.signOut}
    />
  );
}
