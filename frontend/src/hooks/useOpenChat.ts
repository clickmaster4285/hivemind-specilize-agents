"use client";

import { usePathname, useRouter } from "next/navigation";

import { useApp } from "@/components/app/AppProvider";
import { chatIdFromPath, chatPath, NEW_CHAT_PATH } from "@/lib/routes";
import type { ChatThread } from "@/lib/threads";

/** Open a saved chat or start a new one from anywhere (sidebar, Projects, Library). On a chat page ChatUrlSync
 *  puts the chat in the address; from another page we navigate to the chat's own address. */
export function useOpenChat() {
  const app = useApp();
  const router = useRouter();
  const onChatPage = chatIdFromPath(usePathname()) !== undefined;
  return {
    openChat(thread: ChatThread) {
      void app.chat.openThread(thread);
      if (!onChatPage) router.push(chatPath(thread.id));
    },
    newChat() {
      app.newChat();
      if (!onChatPage) router.push(NEW_CHAT_PATH);
    },
  };
}
