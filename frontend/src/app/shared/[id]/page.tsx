"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useApp } from "@/components/app/AppProvider";
import MessageFeed from "@/components/chat/MessageFeed";
import { continueSharedChat, getJobHistory, getSharedChat } from "@/lib/api";
import { attachmentsFromJob, errorText } from "@/lib/chat";
import { buildFeedFromEvents, mergeArtifactsFromJob, STATUS_ID } from "@/lib/feedReducer";
import { newId } from "@/lib/id";
import { chatPath, SHARED_PATH } from "@/lib/routes";
import type { FeedItem } from "@/lib/types";
import { btn } from "@/lib/ui";

const nothing = () => undefined;

/** /shared/<chat id> (BACKLOG L30 T4): a colleague's chat, read-only — the same messages, sources and files as in
 *  their own view, without the composer or the edit / retry buttons. "Continue in my own chat" starts a chat of mine
 *  from its summary. */
export default function SharedChatPage() {
  const app = useApp();
  const router = useRouter();
  const { id: raw } = useParams<{ id: string }>();
  const id = decodeURIComponent(raw);
  const [head, setHead] = useState<{ title: string; owner: string } | null>(null);
  const [feed, setFeed] = useState<FeedItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const chat = await getSharedChat(id);
        if (cancelled) return;
        setHead({ title: chat.title, owner: chat.owner });
        let built: FeedItem[] = [];
        for (const job of chat.jobs) {
          const saved = await getJobHistory(job.id);
          const images = await attachmentsFromJob(saved.job);
          const slice = mergeArtifactsFromJob(
            buildFeedFromEvents(saved.events, saved.job.query, images).filter((item) => item.id !== STATUS_ID), saved.job);
          built = [...built, ...slice.map((item) => (item.kind === "user" ? item : { ...item, jobId: item.jobId || job.id }))];
        }
        if (!cancelled) setFeed(built);
      } catch (err) {
        if (!cancelled) setError(errorText(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  async function continueHere() {
    setBusy(true);
    try {
      const { thread, summary } = await continueSharedChat(id, newId());
      app.chat.startContinuedChat(thread.id, thread.title, summary);
      router.push(chatPath(thread.id));
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  if (!app.auth.user) return null;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <h1 className="m-0 truncate text-base font-semibold [font-family:var(--font-display)]">{head?.title ?? "Shared chat"}</h1>
          <p className="m-0 text-xs text-muted">{head ? `Shared by ${head.owner} · read-only` : "Loading…"}</p>
        </div>
        <div className="flex gap-2">
          <button type="button" className={btn("ghost", "sm")} onClick={() => router.push(SHARED_PATH)}>← Shared with me</button>
          <button type="button" className={btn("primary", "sm")} disabled={busy || !feed} onClick={() => void continueHere()}>
            {busy ? "Summarising…" : "Continue in my own chat"}
          </button>
        </div>
      </div>
      {error ? <p role="alert" className="m-4 text-sm text-danger">{error}</p> : null}
      {feed ? (
        <MessageFeed feed={feed} running={false} activeJobId={null} readOnly
                     onEditSend={nothing} onRetry={nothing} onRegenerate={nothing} onConfirm={nothing} onChoose={nothing}
                     onPickOption={nothing} onClarifySubmit={nothing} onPickSuggestion={nothing} />
      ) : null}
    </div>
  );
}
