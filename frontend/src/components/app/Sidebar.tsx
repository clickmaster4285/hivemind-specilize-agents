"use client";

import { usePathname, useRouter } from "next/navigation";

import { useApp } from "@/components/app/AppProvider";
import ChatSidebar from "@/components/app/ChatSidebar";
import { chatIdFromPath, chatPath, NEW_CHAT_PATH, PLUGINS_PATH, SETTINGS_PATH } from "@/lib/routes";
import type { ChatThread } from "@/lib/threads";

/** The persistent left rail. Reads shared state from the provider. On a chat page ChatUrlSync puts the opened
 *  chat in the address; from another page (Settings, Plugins) we navigate to the chat's own address. */
export default function Sidebar({ collapsed, onToggleCollapsed }: { collapsed: boolean; onToggleCollapsed: () => void }) {
  const app = useApp();
  const router = useRouter();
  const pathname = usePathname();
  const onChatPage = chatIdFromPath(pathname) !== undefined;

  return (
    <ChatSidebar
      username={app.auth.user?.username || ""}
      role={app.auth.user?.role || ""}
      history={app.history}
      collapsed={collapsed}
      onToggleCollapsed={onToggleCollapsed}
      pluginsOpen={pathname === PLUGINS_PATH}
      onNewChat={() => {
        app.newChat();
        if (!onChatPage) router.push(NEW_CHAT_PATH);
      }}
      onOpenThread={(thread: ChatThread) => {
        void app.chat.openThread(thread);
        if (!onChatPage) router.push(chatPath(thread.id));
      }}
      onDeleteThread={(thread: ChatThread) => void app.deleteThread(thread)}
      onOpenPlugins={() => router.push(PLUGINS_PATH)}
      onOpenSettings={() => router.push(SETTINGS_PATH)}
      onSignOut={app.auth.signOut}
    />
  );
}
