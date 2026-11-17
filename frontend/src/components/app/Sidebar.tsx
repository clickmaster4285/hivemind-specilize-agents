"use client";

import { usePathname, useRouter } from "next/navigation";

import { useApp } from "@/components/app/AppProvider";
import ChatSidebar from "@/components/app/ChatSidebar";
import { useOpenChat } from "@/hooks/useOpenChat";
import type { ChatThread } from "@/lib/threads";

/** The persistent left rail. Reads shared state from the provider; opening a chat goes through useOpenChat. */
export default function Sidebar({ collapsed, onToggleCollapsed }: { collapsed: boolean; onToggleCollapsed: () => void }) {
  const app = useApp();
  const router = useRouter();
  const pathname = usePathname();
  const { openChat, newChat } = useOpenChat();

  return (
    <ChatSidebar
      username={app.auth.user?.username || ""}
      role={app.auth.user?.role || ""}
      modules={app.auth.user?.modules || []}
      history={app.history}
      collapsed={collapsed}
      onToggleCollapsed={onToggleCollapsed}
      pathname={pathname || ""}
      onGo={(path) => router.push(path)}
      onNewChat={newChat}
      onOpenThread={openChat}
      onDeleteThread={(thread: ChatThread) => void app.deleteThread(thread)}
      onSignOut={app.auth.signOut}
    />
  );
}
