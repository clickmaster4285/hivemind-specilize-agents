"use client";

import { useRouter } from "next/navigation";
import { useLayoutEffect, useMemo, useRef } from "react";

import { useApp, type AppValue } from "@/components/app/AppProvider";
import Composer from "@/components/chat/Composer";
import MessageFeed from "@/components/chat/MessageFeed";
import { chatMemory } from "@/lib/chat";
import { sumUsage } from "@/lib/format";

/** The chat page content (below the persistent navbar): the run/specialist/model controls, the message
 *  feed, and the composer. All state comes from the shared provider. */
export default function ChatView() {
  const app = useApp();
  const router = useRouter();

  // Every keystroke re-renders the app (the message text lives in it). The feed's actions stay the same
  // functions and call the latest app, so the memoised feed re-renders only when the conversation changes.
  const latest = useRef(app);
  useLayoutEffect(() => {
    latest.current = app;
  });
  const feedActions = useMemo(
    () => ({
      onEditSend: (...a: Parameters<AppValue["editAndResend"]>) => latest.current.editAndResend(...a),
      onRetry: () => void latest.current.retry(),
      onRegenerate: (reason: string) => void latest.current.regenerate(reason),
      onConfirm: (...a: Parameters<AppValue["onConfirm"]>) => latest.current.onConfirm(...a),
      onPickOption: (...a: Parameters<AppValue["pickOption"]>) => latest.current.pickOption(...a),
      onClarifySubmit: (...a: Parameters<AppValue["submitClarify"]>) => latest.current.submitClarify(...a),
      onPickSuggestion: (...a: Parameters<AppValue["pickSuggestion"]>) => latest.current.pickSuggestion(...a),
    }),
    [],
  );
  const feed = app.chat.feed;
  const historyBudget = app.health.health?.context?.history_budget;
  const chatUsage = useMemo(() => sumUsage(feed), [feed]);
  const memory = useMemo(() => chatMemory(feed, historyBudget), [feed, historyBudget]);

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <MessageFeed feed={feed} running={app.chat.running} activeJobId={app.chat.activeJobId} {...feedActions} />

      <Composer
        query={app.query}
        onQuery={app.setQuery}
        inputRef={app.queryRef}
        images={app.images}
        editing={Boolean(app.chat.editingFromId)}
        onCancelEdit={app.cancelEdit}
        running={app.chat.running}
        canStop={app.canStop}
        onStop={app.stop}
        onSend={app.send}
        deep={app.deep}
        onToggleDeep={app.toggleDeep}
        runMode={app.runMode}
        onRunMode={app.setRunMode}
        specialistLabel={app.specialists.label}
        skills={app.skills.usable}
        onOpenSettings={() => router.push("/settings")}
        imageProfile={app.imageProfile}
        onImageProfile={app.setImageProfile}
        editStrength={app.editStrength}
        onEditStrength={app.setEditStrength}
        imagePipeline={app.health.health?.image_pipeline}
        chatUsage={chatUsage}
        chatId={app.history.activeThreadId}
        memory={memory}
        memoryBusy={app.memoryBusy}
        onContinueChat={() => void app.continueInNewChat()}
        onSaveSummary={() => void app.saveSummary()}
      />
    </div>
  );
}
