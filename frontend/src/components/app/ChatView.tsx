"use client";

import { useRouter } from "next/navigation";

import { useApp } from "@/components/app/AppProvider";
import Composer from "@/components/chat/Composer";
import MessageFeed from "@/components/chat/MessageFeed";
import type { RunMode } from "@/lib/types";

/** The chat page content (below the persistent navbar): the run/specialist/model controls, the message
 *  feed, and the composer. All state comes from the shared provider. */
export default function ChatView() {
  const app = useApp();
  const router = useRouter();

  return (
    <div className="chat-view">
     

      <MessageFeed
        feed={app.chat.feed}
        running={app.chat.running}
        activeJobId={app.chat.activeJobId}
        onEdit={(item) => void app.beginEdit(item)}
        onRetry={() => void app.retry()}
        onConfirm={app.onConfirm}
        onPickOption={app.pickOption}
      />

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
        wantOptions={app.wantOptions}
        onToggleOptions={app.toggleOptions}
        runMode={app.runMode}
        specialistLabel={app.specialists.label}
        onOpenSettings={() => router.push("/settings")}
        imageProfile={app.imageProfile}
        onImageProfile={app.setImageProfile}
        editStrength={app.editStrength}
        onEditStrength={app.setEditStrength}
        imagePipeline={app.health.health?.image_pipeline}
      />
    </div>
  );
}
