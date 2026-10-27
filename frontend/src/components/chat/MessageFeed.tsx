"use client";

import type { CSSProperties } from "react";

import LiveStatus from "@/components/LiveStatus";
import MarkdownBody from "@/components/MarkdownBody";
import MessageAvatar from "@/components/MessageAvatar";
import ConfirmCard from "@/components/chat/ConfirmCard";
import { AgentThumbs, ArtifactChips, ClarifyForm, OptionCards, SourceList, SuggestionChips, ToolDrawer, VerificationNote } from "@/components/chat/MessageExtras";
import type { AnswerOption, FeedItem } from "@/lib/types";

// Inline Tailwind building blocks. Only msg-action and the react-markdown output (md-body) stay global CSS.
// The scroll container is full-width (so the scrollbar sits at the Main-area edge, not against the text);
// the content lives in a centered, comfortably-wide column inside it.
const scrollCls =
  "flex-1 min-h-0 w-full overflow-y-auto overflow-x-hidden px-6 pt-4 pb-5 [overscroll-behavior:contain]";
const colCls = "mx-auto flex w-full max-w-[1040px] flex-col gap-5";
const captionCls =
  "m-0 min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-[0.72rem] leading-[1.25] text-[var(--muted)]";

/** Claude/ChatGPT-style column: the assistant answer is plain full-width text (no card); the user's
 *  message stays a bubble on the right; system lines are small centered pills. */
function cardClass(kind: FeedItem["kind"], isError: boolean) {
  if (kind === "user") {
    return "min-w-0 w-fit max-w-[85%] rounded-2xl border px-[0.9rem] py-[0.55rem] border-[color-mix(in_srgb,var(--copper)_40%,var(--line))] bg-[color-mix(in_srgb,var(--copper)_12%,var(--panel-2))]";
  }
  if (kind === "agent") {
    return "min-w-0 w-full";   // no box — the answer reads as a document in the centered column
  }
  // system
  if (isError) {
    return "min-w-0 w-fit max-w-[85%] rounded-[0.45rem] border px-[0.6rem] py-[0.2rem] border-[color-mix(in_srgb,var(--danger)_45%,var(--line))] bg-[rgba(214,90,74,0.12)]";
  }
  return "min-w-0 w-fit max-w-[85%] rounded-full border border-transparent px-[0.6rem] py-[0.15rem] bg-white/[0.045]";
}

/** The conversation: user bubbles, answers (streamed Markdown), status lines. */
export default function MessageFeed({ feed, running, activeJobId, onEdit, onRetry, onConfirm, onPickOption, onClarifySubmit, onPickSuggestion }: Props) {
  const lastUserIdx = feed.reduce((acc, item, i) => (item.kind === "user" ? i : acc), -1);
  const lastAgentIdx = feed.reduce((acc, item, i) => (item.kind === "agent" ? i : acc), -1);

  if (feed.length === 0) {
    return (
      <div className={scrollCls}>
        <div className={`${colCls} h-full`}>
          <div className="m-auto mt-[12vh] max-w-[22rem] text-center text-[var(--muted)]">
            <strong className="mb-[0.4rem] block text-[1.2rem] text-[var(--text)] [font-family:var(--font-display)]">
              Start a conversation
            </strong>
            Ask anything: IT, development or marketing questions go to the matching specialist automatically.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={scrollCls}>
      <div className={colCls}>
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
        if (item.confirm) {
          return <ConfirmCard key={item.id} item={item} onConfirm={onConfirm} />;
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
            onPickOption={onPickOption}
            onClarifySubmit={onClarifySubmit}
            onPickSuggestion={onPickSuggestion}
            isLastAgent={index === lastAgentIdx}
          />
          );
        })}
      </div>
    </div>
  );
}

type Props = {
  feed: FeedItem[];
  running: boolean;
  activeJobId: string | null;
  onEdit: (item: FeedItem) => void;
  onRetry: () => void;
  onConfirm: (commandId: string, approved: boolean) => void;
  onPickOption: (query: string, card: AnswerOption) => void;
  onClarifySubmit: (query: string, picks: { question: string; chosen: string[] }[]) => void;
  onPickSuggestion: (q: string) => void;
};

function MessageRow({
  item,
  isError,
  fallbackJobId,
  showToolbar,
  canRetry,
  running,
  onEdit,
  onRetry,
  onPickOption,
  onClarifySubmit,
  onPickSuggestion,
  isLastAgent,
}: {
  item: FeedItem;
  isError: boolean;
  fallbackJobId: string | null;
  showToolbar: boolean;
  canRetry: boolean;
  running: boolean;
  onEdit: (item: FeedItem) => void;
  onRetry: () => void;
  onPickOption: (query: string, card: AnswerOption) => void;
  onClarifySubmit: (query: string, picks: { question: string; chosen: string[] }[]) => void;
  onPickSuggestion: (q: string) => void;
  isLastAgent: boolean;
}) {
  const rowAlign = item.kind === "user" ? "justify-end" : item.kind === "system" ? "items-center justify-center" : "";
  const labelCls = `font-semibold ${isError ? "text-[#ffb4aa]" : "text-[var(--muted)]"}`;
  const metaCls = isError ? "text-[#ffb4aa]" : "text-[var(--muted)]";
  return (
    <article
      className={`flex w-full max-w-full items-start ${rowAlign}`}
      style={item.accent ? ({ ["--accent"]: item.accent } as CSSProperties) : undefined}
    >
      <div className={cardClass(item.kind, isError)}>
        {item.kind === "system" ? (
          <p className={`m-0 text-[0.72rem] leading-[1.25] ${isError ? "text-[#ffb4aa]" : "text-[var(--muted)]"}`}>
            <span className={labelCls}>{item.title}</span>
            {item.body ? <span className={metaCls}> {item.body}</span> : null}
          </p>
        ) : (
          <>
            {item.kind === "agent" ? (
              <header className="mb-[0.3rem] flex min-w-0 items-center gap-[0.4rem]">
                <MessageAvatar kind="agent" title={item.title} accent={item.accent} />
                <p className={captionCls}>
                  <span className="font-semibold text-[var(--muted)]">{item.title}</span>
                  {item.meta ? <span className="text-[var(--muted)]"> · {item.meta}</span> : null}
                </p>
              </header>
            ) : null}
            {item.kind === "user" ? (
              <div className="whitespace-pre-wrap text-[0.86rem] leading-[1.35]">
                {item.images?.length ? (
                  <div className="mb-[0.45rem] flex flex-wrap items-start gap-[0.4rem]">
                    {item.images.map((img) => (
                      <a
                        key={img.url}
                        href={img.url}
                        target="_blank"
                        rel="noreferrer"
                        className="relative block h-[4.4rem] w-[4.4rem] overflow-hidden rounded-[0.45rem] border border-[var(--line)] bg-[rgba(0,0,0,0.25)]"
                      >
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={img.url} alt={img.filename} className="block h-full w-full object-cover" />
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
                  <MarkdownBody content={item.body} pending={item.pending && !item.streaming} sources={item.sources} />
                ) : null}
                {item.pending ? (
                  <LiveStatus text={item.statusText} steps={item.steps} startedAt={item.startedAt} />
                ) : null}
                <OptionCards item={item} onPick={onPickOption} />
                <ClarifyForm item={item} onSubmit={onClarifySubmit} />
                <VerificationNote item={item} />
                <SourceList item={item} />
                {isLastAgent ? <SuggestionChips item={item} onPick={onPickSuggestion} /> : null}
              </>
            )}
            {showToolbar ? (
              <div className={`mt-[0.35rem] flex flex-wrap gap-[0.3rem] ${item.kind === "user" ? "justify-end" : ""}`}>
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
