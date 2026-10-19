"use client";

import type { CSSProperties } from "react";

import LiveStatus from "@/components/LiveStatus";
import MarkdownBody from "@/components/MarkdownBody";
import MessageAvatar from "@/components/MessageAvatar";
import { AgentThumbs, ArtifactChips, SourceList, ToolDrawer, VerificationNote } from "@/components/chat/MessageExtras";
import type { FeedItem } from "@/lib/types";

type Props = {
  feed: FeedItem[];
  running: boolean;
  activeJobId: string | null;
  onEdit: (item: FeedItem) => void;
  onRetry: () => void;
};

/** The conversation: user bubbles, answers (streamed Markdown), status lines. */
export default function MessageFeed({ feed, running, activeJobId, onEdit, onRetry }: Props) {
  const lastUserIdx = feed.reduce((acc, item, i) => (item.kind === "user" ? i : acc), -1);
  const lastAgentIdx = feed.reduce((acc, item, i) => (item.kind === "agent" ? i : acc), -1);

  if (feed.length === 0) {
    return (
      <div className="feed chat-feed">
        <div className="empty hero-empty">
          <strong>Start a conversation</strong>
          Ask anything: IT, development or marketing questions go to the matching specialist automatically.
        </div>
      </div>
    );
  }

  return (
    <div className="feed chat-feed">
      {feed.map((item, index) => {
        // skip a user bubble that repeats the one just before it (same text, no images)
        const previous = feed[index - 1];
        if (
          item.kind === "user" &&
          previous?.kind === "user" &&
          previous.body.trim() === item.body.trim() &&
          !item.images?.length
        ) {
          return null;
        }
        const isError = item.kind === "system" && (item.meta === "error" || item.title === "Error");
        const isLastError = isError && index === feed.length - 1;
        return (
          <MessageRow
            key={item.id}
            item={item}
            isError={isError}
            fallbackJobId={activeJobId}
            showToolbar={item.kind === "user" || index === lastAgentIdx || isLastError}
            canRetry={!running && (index === lastUserIdx || index === lastAgentIdx || isLastError)}
            running={running}
            onEdit={onEdit}
            onRetry={onRetry}
          />
        );
      })}
    </div>
  );
}

function MessageRow({
  item,
  isError,
  fallbackJobId,
  showToolbar,
  canRetry,
  running,
  onEdit,
  onRetry,
}: {
  item: FeedItem;
  isError: boolean;
  fallbackJobId: string | null;
  showToolbar: boolean;
  canRetry: boolean;
  running: boolean;
  onEdit: (item: FeedItem) => void;
  onRetry: () => void;
}) {
  return (
    <article
      className={`msg-row ${item.kind}${item.pending ? " pending" : ""}${isError ? " error" : ""}`}
      style={item.accent ? ({ ["--accent"]: item.accent } as CSSProperties) : undefined}
    >
      <div className="msg-card">
        {item.kind === "system" ? (
          <p className="msg-line">
            <span className="msg-label">{item.title}</span>
            {item.body ? <span className="msg-meta"> {item.body}</span> : null}
          </p>
        ) : (
          <>
            {item.kind === "agent" ? (
              <header className="msg-head">
                <MessageAvatar kind="agent" title={item.title} accent={item.accent} />
                <p className="msg-caption">
                  <span className="msg-label">{item.title}</span>
                  {item.meta ? <span className="msg-meta"> · {item.meta}</span> : null}
                </p>
              </header>
            ) : null}
            {item.kind === "user" ? (
              <div className="bubble-body">
                {item.images?.length ? (
                  <div className="chat-thumbs">
                    {item.images.map((img) => (
                      <a key={img.url} href={img.url} target="_blank" rel="noreferrer">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={img.url} alt={img.filename} />
                      </a>
                    ))}
                  </div>
                ) : null}
                {item.body}
              </div>
            ) : (
              <>
                <AgentThumbs item={item} fallbackJobId={fallbackJobId} />
                <ArtifactChips item={item} fallbackJobId={fallbackJobId} />
                {item.body ? (
                  // streamed text renders as Markdown while it grows
                  <MarkdownBody content={item.body} pending={item.pending && !item.streaming} />
                ) : null}
                {item.pending ? <LiveStatus text={item.statusText} startedAt={item.startedAt} /> : null}
                <VerificationNote item={item} />
                <SourceList item={item} />
              </>
            )}
            {showToolbar ? (
              <div className="msg-toolbar">
                {item.kind === "user" ? (
                  <button type="button" className="msg-action" disabled={running} onClick={() => onEdit(item)}>
                    Edit
                  </button>
                ) : null}
                {canRetry ? (
                  <button type="button" className="msg-action" onClick={onRetry}>
                    Retry
                  </button>
                ) : null}
              </div>
            ) : null}
            <ToolDrawer item={item} />
          </>
        )}
      </div>
    </article>
  );
}
