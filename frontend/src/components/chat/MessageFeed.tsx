"use client";

import { Fragment, memo, useEffect, useRef, useState, type CSSProperties, type UIEvent } from "react";

import LiveStatus from "@/components/chat/LiveStatus";
import MarkdownBody from "@/components/answer/MarkdownBody";
import MessageAvatar from "@/components/chat/MessageAvatar";
import ConfirmCard from "@/components/chat/ConfirmCard";
import { IconChevronDown } from "@/components/chat/Icons";
import PromptRail, { promptAnchorId } from "@/components/chat/PromptRail";
import { AgentThumbs, AnswerActions, ArtifactChips, ClarifyForm, OptionCards, QuizCard, SourceList, SuggestionChips, ToolDrawer, useWebImages, VerificationNote } from "@/components/chat/MessageExtras";
import type { AnswerOption, ChatImage, FeedItem } from "@/lib/types";
import { composerColumn, msgAction } from "@/lib/ui";

// Inline Tailwind building blocks. Shared ones (buttons, message actions) are in lib/ui.ts.
// The scroll container is full-width (so the scrollbar sits at the Main-area edge, not against the text);
// the content lives in a centered, comfortably-wide column inside it.
const scrollCls =
  "flex-1 min-h-0 w-full overflow-y-auto overflow-x-hidden px-6 pt-4 pb-5 [overscroll-behavior:contain]";
const colCls = "mx-auto flex w-full max-w-[1040px] flex-col gap-5";
// Within this many pixels of the end counts as "at the bottom": the streamed answer is followed only then, so
// scrolling up to read is not undone by each new piece of text (ISSUES_LOG O80).
const BOTTOM_SLACK_PX = 48;
// A question counts as "in view" (highlighted in the prompt rail, O78) once its top is this far into the feed.
const ACTIVE_OFFSET_PX = 120;
// The jump button sits right above the send arrow: the composer's column, its right edge inset by the message box's
// padding and border (pr-1 + 1px), and the arrow's size (2.1rem), so the two line up (user, O79)
const jumpStripCls = `pointer-events-none absolute inset-x-0 bottom-3 flex justify-end ${composerColumn}`;
const jumpCls =
  "pointer-events-auto mr-[calc(0.25rem+1px)] grid h-[2.1rem] w-[2.1rem] place-items-center rounded-full border " +
  "border-line bg-panel text-text shadow-[0_2px_8px_rgba(0,0,0,0.25)] hover:bg-panel-2";
const captionCls =
  "m-0 min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-[0.72rem] leading-[1.25] text-muted";

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
    return "min-w-0 w-fit max-w-[85%] rounded-[0.45rem] border px-[0.6rem] py-[0.2rem] border-[color-mix(in_srgb,var(--danger)_45%,var(--line))] bg-danger-soft";
  }
  return "min-w-0 w-fit max-w-[85%] rounded-full border border-transparent px-[0.6rem] py-[0.15rem] bg-hover";
}

/** Inline edit of a user message (ChatGPT-style): the bubble becomes a textarea with Cancel / Send. */
function InlineEdit({ initial, onCancel, onSend }: { initial: string; onCancel: () => void; onSend: (text: string) => void }) {
  const [text, setText] = useState(initial);
  const trimmed = text.trim();
  return (
    <div className="w-full rounded-2xl border border-[color-mix(in_srgb,var(--copper)_45%,var(--line))] bg-panel-2 px-[0.8rem] py-[0.6rem]">
      <textarea
        autoFocus
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (trimmed) onSend(trimmed);
          } else if (e.key === "Escape") {
            onCancel();
          }
        }}
        rows={Math.min(10, Math.max(1, text.split("\n").length))}
        className="w-full resize-none border-0 bg-transparent text-[0.86rem] leading-[1.4] text-text outline-none placeholder:text-muted"
      />
      <div className="mt-[0.45rem] flex justify-end gap-[0.4rem]">
        <button type="button" className={msgAction()} onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className={msgAction("accent")}
          disabled={!trimmed}
          onClick={() => onSend(trimmed)}
        >
          Send
        </button>
      </div>
    </div>
  );
}

/** The conversation: user bubbles, answers (streamed Markdown), status lines. */
function MessageFeed({ feed, running, activeJobId, onEditSend, onRetry, onRegenerate, onConfirm, onPickOption, onClarifySubmit, onPickSuggestion }: Props) {
  const lastUserIdx = feed.reduce((acc, item, i) => (item.kind === "user" ? i : acc), -1);
  const lastAgentIdx = feed.reduce((acc, item, i) => (item.kind === "agent" ? i : acc), -1);
  const [editingId, setEditingId] = useState<string | null>(null);

  // Follow the newest message only while the reader is at the bottom (O80): scrolling up to read stops it; a new
  // question, another chat, or the jump button (O79) starts it again.
  const bottomRef = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const [atBottom, setAtBottom] = useState(true);
  const [activePrompt, setActivePrompt] = useState<string | null>(null);
  const prompts = feed.filter((item) => item.kind === "user").map((item) => ({ id: item.id, text: item.body }));
  const last = feed[feed.length - 1];
  const toBottom = (smooth = false) => {
    following.current = true;
    bottomRef.current?.scrollIntoView({ block: "end", behavior: smooth ? "smooth" : "auto" });
  };
  const onScroll = (e: UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_SLACK_PX;
    following.current = near;
    setAtBottom(near);
    // the last question whose top has scrolled past the offset is the one being read (null = the newest)
    const passed = prompts.filter((p) => {
      const anchor = document.getElementById(promptAnchorId(p.id));
      return anchor !== null && anchor.offsetTop <= el.scrollTop + ACTIVE_OFFSET_PX;
    });
    setActivePrompt(near ? null : (passed[passed.length - 1]?.id ?? prompts[0]?.id ?? null));
  };
  const jumpTo = (id: string) => {
    document.getElementById(promptAnchorId(id))?.scrollIntoView({ block: "start", behavior: "smooth" });
  };
  const firstId = feed[0]?.id;
  useEffect(() => {
    // another chat opened, or the user asked something new: follow again from the bottom
    following.current = true;
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [firstId, lastUserIdx]);
  useEffect(() => {
    if (following.current) bottomRef.current?.scrollIntoView({ block: "end" });
  }, [feed.length, last?.id, last?.body?.length, last?.pending, last?.streaming]);

  return (
    <div className="relative flex min-h-0 w-full flex-1 flex-col">
    <div className={scrollCls} onScroll={onScroll}>
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
          <Fragment key={item.id}>
          {item.kind === "user" && <span id={promptAnchorId(item.id)} aria-hidden />}
          <MessageRow
            item={item}
            isError={isError}
            fallbackJobId={activeJobId}
            showToolbar={item.kind === "user" || index === lastAgentIdx || isLastError}
            canRetry={!running && (index === lastUserIdx || index === lastAgentIdx || isLastError)}
            running={running}
            editing={editingId === item.id}
            onStartEdit={() => setEditingId(item.id)}
            onCancelEdit={() => setEditingId(null)}
            onSubmitEdit={(text) => {
              setEditingId(null);
              onEditSend(item, text);
            }}
            onRetry={onRetry}
            onRegenerate={onRegenerate}
            onPickOption={onPickOption}
            onClarifySubmit={onClarifySubmit}
            onPickSuggestion={onPickSuggestion}
            isLastAgent={index === lastAgentIdx}
          />
          </Fragment>
          );
        })}
        <div ref={bottomRef} aria-hidden />
      </div>
    </div>
    <PromptRail prompts={prompts} active={activePrompt} onJump={jumpTo} />
    {!atBottom && (
      <div className={jumpStripCls}>
        <button type="button" className={jumpCls} aria-label="Jump to the latest message" title="Jump to the latest message"
          onClick={() => toBottom(true)}>
          <IconChevronDown />
        </button>
      </div>
    )}
    </div>
  );
}

type Props = {
  feed: FeedItem[];
  running: boolean;
  activeJobId: string | null;
  onEditSend: (item: FeedItem, text: string) => void;
  onRetry: () => void;
  onRegenerate: (reason: string) => void;
  onConfirm: (commandId: string, approved: boolean) => void;
  onPickOption: (query: string, card: AnswerOption) => void;
  onClarifySubmit: (query: string, picks: { question: string; chosen: string[] }[]) => void;
  onPickSuggestion: (q: string) => void;
};

/** Stable key tying a heading to its images — must match the backend's _norm_anchor (web_images.py):
 *  NFKD + strip markup + drop non-alphanumerics, so emoji / accents / numbering fold away the same way. */
function normAnchor(text: string): string {
  return text
    .normalize("NFKD")
    .replace(/[*_`#]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

/** A line that starts with a bold label ("1. **Istanbul**: …", "- **Seoul:** …") — the backend's _BOLD_ITEM_RE. */
const BOLD_ITEM_RE = /^\s*(?:[-*•]|\d+[.)])?\s*\*\*(.+?)\*\*/;

/** Split an answer into sections at Markdown headings (## / ### …) and at bold list items that have pictures
 *  (`itemKeys`, O75), keeping each such line with the body that follows it. `key` is its anchor (normAnchor);
 *  the first section (before any) has key "". Fenced code is left intact. */
function splitSections(md: string, itemKeys: Set<string>): { key: string; text: string }[] {
  const out: { key: string; text: string }[] = [];
  let key = "";
  let buf: string[] = [];
  let fence: string | null = null;
  const headingRe = /^\s{0,3}#{1,4}\s+(.+?)\s*#*\s*$/;
  const flush = () => out.push({ key, text: buf.join("\n") });
  for (const line of md.split("\n")) {
    if (fence) {
      buf.push(line);
      if (line.trimStart().startsWith(fence)) fence = null;
      continue;
    }
    const open = line.match(/^\s*(```|~~~)/);
    if (open) {
      fence = open[1];
      buf.push(line);
      continue;
    }
    const h = line.match(headingRe);
    const item = h ? null : line.match(BOLD_ITEM_RE);
    const itemKey = item ? normAnchor(item[1]) : "";
    if (h || (itemKey && itemKeys.has(itemKey))) {
      flush();
      key = h ? normAnchor(h[1].trim()) : itemKey;
      buf = [line];
    } else {
      buf.push(line);
    }
  }
  flush();
  return out;
}

/** One group of a section's web images, laid out by how many there are: a lone image is a hero; a few
 *  tile into a row at a shared height; more pack into a grid whose last tile shows a "+N" overflow.
 *  object-contain keeps diagrams readable (no cropping); click any image to open it full size. */
function AnswerImages({ images }: { images: ChatImage[] }) {
  if (!images.length) return null;
  if (images.length === 1) {
    const img = images[0];
    return (
      <a
        href={img.url}
        target="_blank"
        rel="noreferrer"
        title="Open image"
        className="mt-[0.6rem] block w-fit max-w-full overflow-hidden rounded-[0.6rem] border border-line bg-sunken hover:border-teal"
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={img.url} alt={img.filename} loading="lazy" className="block h-auto max-h-72 w-auto max-w-full object-contain" />
      </a>
    );
  }
  const shown = images.slice(0, 4);
  const extra = images.length - shown.length;
  const h = images.length === 2 ? "h-[11rem]" : images.length === 3 ? "h-[9.5rem]" : "h-34";
  return (
    <div className="mt-[0.6rem] flex flex-wrap items-start gap-[0.4rem]">
      {shown.map((img, i) => (
        <a
          key={img.url}
          href={img.url}
          target="_blank"
          rel="noreferrer"
          title="Open image"
          className="relative block overflow-hidden rounded-lg border border-line bg-sunken hover:border-teal"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={img.url} alt={img.filename} loading="lazy" className={`block ${h} w-auto max-w-full object-contain`} />
          {i === shown.length - 1 && extra > 0 ? (
            <span className="absolute inset-0 flex items-center justify-center bg-black/55 text-[1.1rem] font-semibold text-white">
              +{extra}
            </span>
          ) : null}
        </a>
      ))}
    </div>
  );
}

/** Where a section's photos are on their way: shimmering tiles the size of a typical image row, so the
 *  layout doesn't jump when the real images land. */
function ImagesPlaceholder() {
  return (
    <div className="mt-[0.6rem] flex flex-wrap gap-[0.4rem]" role="status" aria-label="Loading images">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-34 w-48 max-w-full animate-pulse rounded-lg border border-line bg-panel-2"
          style={{ animationDelay: `${i * 150}ms` }}
        />
      ))}
    </div>
  );
}

/** The assistant answer, with its web images placed under the section each one illustrates (context-aware):
 *  images are grouped by their anchor and dropped in right after the matching heading or bold list item, so a
 *  React diagram sits under "React" and an Istanbul photo after "1. **Istanbul**". Unanchored images lead the answer; any whose
 *  section isn't found trail at the end so nothing is lost. Sections still waiting for photos (announced by
 *  the backend, or fetched but not loaded yet) show placeholders. While streaming / imageless, plain Markdown. */
function AnswerContent({ item, webImgs, loading }: { item: FeedItem; webImgs: ChatImage[]; loading: boolean }) {
  if (!item.body) return null;
  const done = !item.pending && !item.streaming;
  const waiting = new Set(item.imagesPending ?? (loading ? (item.webImages || []).map((img) => img.anchor || "") : []));
  if (!done || (webImgs.length === 0 && waiting.size === 0)) {
    return <MarkdownBody content={item.body} pending={item.pending && !item.streaming} sources={item.sources} />;
  }
  const byAnchor = new Map<string, ChatImage[]>();
  for (const img of webImgs) {
    const key = img.anchor || "";
    const list = byAnchor.get(key);
    if (list) list.push(img);
    else byAnchor.set(key, [img]);
  }
  const used = new Set<string>();
  const sections = splitSections(item.body, new Set([...byAnchor.keys(), ...waiting]));
  const intro = byAnchor.get("");
  if (intro) used.add("");
  const nodes = sections.map((s, i) => {
    const key = s.key;
    const imgs = key && !used.has(key) ? byAnchor.get(key) : undefined;
    if (imgs) used.add(key);
    return (
      <Fragment key={i}>
        <MarkdownBody content={s.text} sources={item.sources} />
        {i === 0 && intro ? <AnswerImages images={intro} /> : null}
        {imgs ? <AnswerImages images={imgs} /> : key && waiting.has(key) ? <ImagesPlaceholder /> : null}
      </Fragment>
    );
  });
  const leftover = webImgs.filter((img) => !used.has(img.anchor || ""));
  return (
    <>
      {nodes}
      {leftover.length ? <AnswerImages images={leftover} /> : null}
    </>
  );
}

function MessageRow({
  item,
  isError,
  fallbackJobId,
  showToolbar,
  canRetry,
  running,
  editing,
  onStartEdit,
  onCancelEdit,
  onSubmitEdit,
  onRetry,
  onRegenerate,
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
  editing: boolean;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onSubmitEdit: (text: string) => void;
  onRetry: () => void;
  onRegenerate: (reason: string) => void;
  onPickOption: (query: string, card: AnswerOption) => void;
  onClarifySubmit: (query: string, picks: { question: string; chosen: string[] }[]) => void;
  onPickSuggestion: (q: string) => void;
  isLastAgent: boolean;
}) {
  const { images: webImgs, loading: webImgsLoading } = useWebImages(item, fallbackJobId);
  const rowAlign = item.kind === "user" ? "justify-end" : item.kind === "system" ? "items-center justify-center" : "";
  const labelCls = `font-semibold ${isError ? "text-danger-text" : "text-muted"}`;
  const metaCls = isError ? "text-danger-text" : "text-muted";
  return (
    <article
      className={`flex w-full max-w-full items-start ${editing ? "" : rowAlign}`}
      style={item.accent ? ({ ["--accent"]: item.accent } as CSSProperties) : undefined}
    >
      {editing && item.kind === "user" ? (
        <InlineEdit initial={item.body} onCancel={onCancelEdit} onSend={onSubmitEdit} />
      ) : (
      <div className={cardClass(item.kind, isError)}>
        {item.kind === "system" ? (
          <p className={`m-0 text-[0.72rem] leading-tight ${isError ? "text-danger-text" : "text-muted"}`}>
            <span className={labelCls}>{item.title}</span>
            {item.body ? <span className={metaCls}> {item.body}</span> : null}
          </p>
        ) : (
          <>
            {item.kind === "agent" ? (
              <header className="mb-[0.3rem] flex min-w-0 items-center gap-[0.4rem]">
                <MessageAvatar kind="agent" title={item.title} accent={item.accent} />
                <p className={captionCls}>
                  <span className="font-semibold text-muted">{item.title}</span>
                  {item.meta ? <span className="text-muted"> · {item.meta}</span> : null}
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
                        className="relative block h-[4.4rem] w-[4.4rem] overflow-hidden rounded-[0.45rem] border border-line bg-sunken"
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
                {/* answer text; once done, web images are spread between its blocks at varied sizes */}
                <AnswerContent item={item} webImgs={webImgs} loading={webImgsLoading} />
                {item.pending ? (
                  <LiveStatus text={item.statusText} steps={item.steps} startedAt={item.startedAt} />
                ) : null}
                <OptionCards item={item} onPick={onPickOption} />
                <ClarifyForm item={item} onSubmit={onClarifySubmit} />
                <VerificationNote item={item} />
                <SourceList item={item} />
                {isLastAgent ? <QuizCard item={item} /> : null}
                {isLastAgent ? <SuggestionChips item={item} onPick={onPickSuggestion} /> : null}
                {!item.pending && !item.options?.length && !item.clarify?.length ? (
                  <AnswerActions
                    text={item.body}
                    jobId={item.jobId || fallbackJobId}
                    usage={item.usage}
                    onRetry={isLastAgent && !running ? onRetry : undefined}
                    onRegenerate={isLastAgent && !running ? onRegenerate : undefined}
                  />
                ) : null}
              </>
            )}
            {showToolbar ? (
              <div className={`mt-[0.35rem] flex flex-wrap gap-[0.3rem] ${item.kind === "user" ? "justify-end" : ""}`}>
                {item.kind === "user" ? (
                  <button type="button" className={msgAction()} disabled={running} onClick={onStartEdit}>
                    Edit
                  </button>
                ) : null}
                {canRetry && item.kind !== "agent" ? (
                  <button type="button" className={msgAction()} onClick={onRetry}>
                    Retry
                  </button>
                ) : null}
              </div>
            ) : null}
            <ToolDrawer item={item} />
          </>
        )}
      </div>
      )}
    </article>
  );
}

/** Memoised: typing in the message box re-renders the page; the conversation re-renders only when it changes. */
export default memo(MessageFeed);
