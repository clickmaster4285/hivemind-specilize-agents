"use client";

import { Fragment, useEffect, useRef, useState, type CSSProperties } from "react";

import LiveStatus from "@/components/LiveStatus";
import MarkdownBody from "@/components/MarkdownBody";
import MessageAvatar from "@/components/MessageAvatar";
import ConfirmCard from "@/components/chat/ConfirmCard";
import { AgentThumbs, AnswerActions, ArtifactChips, ClarifyForm, OptionCards, QuizCard, SourceList, SuggestionChips, ToolDrawer, useWebImages, VerificationNote } from "@/components/chat/MessageExtras";
import type { AnswerOption, ChatImage, FeedItem } from "@/lib/types";

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

/** Inline edit of a user message (ChatGPT-style): the bubble becomes a textarea with Cancel / Send. */
function InlineEdit({ initial, onCancel, onSend }: { initial: string; onCancel: () => void; onSend: (text: string) => void }) {
  const [text, setText] = useState(initial);
  const trimmed = text.trim();
  return (
    <div className="w-full rounded-2xl border border-[color-mix(in_srgb,var(--copper)_45%,var(--line))] bg-[var(--panel-2)] px-[0.8rem] py-[0.6rem]">
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
        className="w-full resize-none border-0 bg-transparent text-[0.86rem] leading-[1.4] text-[var(--text)] outline-none placeholder:text-[var(--muted)]"
      />
      <div className="mt-[0.45rem] flex justify-end gap-[0.4rem]">
        <button type="button" className="msg-action" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className="msg-action !border-[var(--copper)] !bg-[color-mix(in_srgb,var(--copper)_16%,var(--panel-2))] !text-[var(--text)] disabled:!opacity-45"
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
export default function MessageFeed({ feed, running, activeJobId, onEditSend, onRetry, onRegenerate, onConfirm, onPickOption, onClarifySubmit, onPickSuggestion }: Props) {
  const lastUserIdx = feed.reduce((acc, item, i) => (item.kind === "user" ? i : acc), -1);
  const lastAgentIdx = feed.reduce((acc, item, i) => (item.kind === "agent" ? i : acc), -1);
  const [editingId, setEditingId] = useState<string | null>(null);

  // Keep the newest message in view: on open (feed replaced), on new turns, and while an answer streams.
  const bottomRef = useRef<HTMLDivElement>(null);
  const last = feed[feed.length - 1];
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [feed.length, last?.id, last?.body?.length, last?.pending, last?.streaming]);

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
          );
        })}
        <div ref={bottomRef} aria-hidden />
      </div>
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

/** Split an answer into sections at Markdown headings (## / ### …), keeping each heading line with the
 *  body that follows it. Fenced code is left intact. The first section (before any heading) has heading "". */
function splitSections(md: string): { heading: string; text: string }[] {
  const out: { heading: string; text: string }[] = [];
  let heading = "";
  let buf: string[] = [];
  let fence: string | null = null;
  const headingRe = /^\s{0,3}#{1,4}\s+(.+?)\s*#*\s*$/;
  const flush = () => out.push({ heading, text: buf.join("\n") });
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
    if (h) {
      flush();
      heading = h[1].trim();
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
        className="mt-[0.6rem] block w-fit max-w-full overflow-hidden rounded-[0.6rem] border border-[var(--line)] bg-[rgba(0,0,0,0.25)] hover:border-[var(--teal)]"
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={img.url} alt={img.filename} loading="lazy" className="block h-auto max-h-[18rem] w-auto max-w-full object-contain" />
      </a>
    );
  }
  const shown = images.slice(0, 4);
  const extra = images.length - shown.length;
  const h = images.length === 2 ? "h-[11rem]" : images.length === 3 ? "h-[9.5rem]" : "h-[8.5rem]";
  return (
    <div className="mt-[0.6rem] flex flex-wrap items-start gap-[0.4rem]">
      {shown.map((img, i) => (
        <a
          key={img.url}
          href={img.url}
          target="_blank"
          rel="noreferrer"
          title="Open image"
          className="relative block overflow-hidden rounded-[0.5rem] border border-[var(--line)] bg-[rgba(0,0,0,0.25)] hover:border-[var(--teal)]"
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

/** The assistant answer, with its web images placed under the section each one illustrates (context-aware):
 *  images are grouped by their anchor and dropped in right after the matching heading, so a React diagram
 *  sits under "React" and Istanbul photos under "Istanbul". Unanchored images lead the answer; any whose
 *  section isn't found trail at the end so nothing is lost. While streaming / imageless, it's plain Markdown. */
function AnswerContent({ item, webImgs }: { item: FeedItem; webImgs: ChatImage[] }) {
  if (!item.body) return null;
  const done = !item.pending && !item.streaming;
  if (!done || webImgs.length === 0) {
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
  const sections = splitSections(item.body);
  const intro = byAnchor.get("");
  if (intro) used.add("");
  const nodes = sections.map((s, i) => {
    const key = s.heading ? normAnchor(s.heading) : "";
    const imgs = s.heading && !used.has(key) ? byAnchor.get(key) : undefined;
    if (imgs) used.add(key);
    return (
      <Fragment key={i}>
        <MarkdownBody content={s.text} sources={item.sources} />
        {i === 0 && intro ? <AnswerImages images={intro} /> : null}
        {imgs ? <AnswerImages images={imgs} /> : null}
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
  const webImgs = useWebImages(item, fallbackJobId);
  const rowAlign = item.kind === "user" ? "justify-end" : item.kind === "system" ? "items-center justify-center" : "";
  const labelCls = `font-semibold ${isError ? "text-[#ffb4aa]" : "text-[var(--muted)]"}`;
  const metaCls = isError ? "text-[#ffb4aa]" : "text-[var(--muted)]";
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
                {/* answer text; once done, web images are spread between its blocks at varied sizes */}
                <AnswerContent item={item} webImgs={webImgs} />
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
                    onRetry={isLastAgent && !running ? onRetry : undefined}
                    onRegenerate={isLastAgent && !running ? onRegenerate : undefined}
                  />
                ) : null}
              </>
            )}
            {showToolbar ? (
              <div className={`mt-[0.35rem] flex flex-wrap gap-[0.3rem] ${item.kind === "user" ? "justify-end" : ""}`}>
                {item.kind === "user" ? (
                  <button type="button" className="msg-action" disabled={running} onClick={onStartEdit}>
                    Edit
                  </button>
                ) : null}
                {canRetry && item.kind !== "agent" ? (
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
      )}
    </article>
  );
}
