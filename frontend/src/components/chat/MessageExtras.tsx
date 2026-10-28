"use client";

/** The parts shown under a message: generated images, downloads, the source check, sources, tools. */
import { useEffect, useState, type ReactNode } from "react";

import { fetchJobAttachment, sendFeedback } from "@/lib/api";
import { errorText, timeAgo } from "@/lib/chat";
import type { AnswerOption, ChatImage, FeedItem } from "@/lib/types";

type ItemProps = { item: FeedItem; fallbackJobId: string | null };

/** Auto-clarify: the model's multiple-choice questions. Tick options, then Submit to get the answer. */
export function ClarifyForm({
  item,
  onSubmit,
}: {
  item: FeedItem;
  onSubmit?: (query: string, picks: { question: string; chosen: string[] }[]) => void;
}) {
  const questions = item.clarify || [];
  const [picks, setPicks] = useState<Record<number, string[]>>({});
  if (item.pending || !questions.length) return null;
  const toggle = (qi: number, opt: string) =>
    setPicks((prev) => {
      const cur = prev[qi] || [];
      return { ...prev, [qi]: cur.includes(opt) ? cur.filter((o) => o !== opt) : [...cur, opt] };
    });
  const anyPicked = questions.some((_, i) => (picks[i]?.length || 0) > 0);
  const submit = () =>
    onSubmit?.(
      item.clarifyQuery || "",
      questions.map((q, i) => ({ question: q.question, chosen: picks[i] || [] })),
    );
  return (
    <div className="mt-[0.5rem] flex flex-col gap-[0.6rem]">
      {questions.map((q, qi) => (
        <div key={qi} className="flex flex-col gap-[0.3rem]">
          <span className="text-[0.82rem] font-semibold text-[var(--text)]">{q.question}</span>
          <div className="flex flex-wrap gap-[0.35rem]">
            {q.options.map((opt) => {
              const on = (picks[qi] || []).includes(opt);
              return (
                <button
                  key={opt}
                  type="button"
                  aria-pressed={on}
                  onClick={() => toggle(qi, opt)}
                  className={`rounded-full border px-[0.6rem] py-[0.2rem] text-[0.78rem] cursor-pointer ${
                    on
                      ? "border-[var(--teal)] bg-[color-mix(in_srgb,var(--teal)_18%,transparent)] text-[var(--text)]"
                      : "border-[var(--line)] bg-transparent text-[var(--muted)] hover:text-[var(--text)]"
                  }`}
                >
                  {opt}
                </button>
              );
            })}
          </div>
        </div>
      ))}
      <div>
        <button
          type="button"
          onClick={submit}
          disabled={!anyPicked}
          className="rounded-[0.5rem] border border-[color-mix(in_srgb,var(--teal)_45%,var(--line))] bg-[color-mix(in_srgb,var(--teal)_14%,var(--panel-2))] px-[0.8rem] py-[0.3rem] text-[0.8rem] text-[var(--text)] hover:brightness-[1.08] disabled:cursor-not-allowed disabled:opacity-45"
        >
          Submit
        </button>
      </div>
    </div>
  );
}

/** A short multiple-choice self-check under an educational answer: pick, then "Check answers". */
export function QuizCard({ item }: { item: FeedItem }) {
  const quiz = item.quiz || [];
  const [picks, setPicks] = useState<Record<number, number>>({});
  const [checked, setChecked] = useState(false);
  if (item.pending || !quiz.length) return null;
  const score = quiz.filter((q, i) => picks[i] === q.correct).length;
  return (
    <details
      open
      className="mt-[0.6rem] rounded-[0.6rem] border border-[var(--line)] bg-[color-mix(in_srgb,var(--teal)_6%,var(--panel-2))] px-[0.7rem] py-[0.5rem] text-[0.82rem]"
    >
      <summary className="cursor-pointer font-semibold text-[var(--muted)]">Quick self-check · {quiz.length} questions</summary>
      <div className="mt-[0.45rem] flex flex-col gap-[0.6rem]">
        {quiz.map((q, qi) => (
          <div key={qi} className="flex flex-col gap-[0.25rem]">
            <span className="font-semibold text-[var(--text)]">
              {qi + 1}. {q.question}
            </span>
            <div className="flex flex-col gap-[0.2rem]">
              {q.options.map((opt, oi) => {
                const picked = picks[qi] === oi;
                const isCorrect = oi === q.correct;
                let cls = "border-[var(--line)] text-[var(--muted)] hover:text-[var(--text)]";
                if (checked && isCorrect)
                  cls = "border-[var(--teal)] text-[var(--text)] bg-[color-mix(in_srgb,var(--teal)_14%,transparent)]";
                else if (checked && picked) cls = "border-[var(--danger)] text-[var(--text)]";
                else if (picked) cls = "border-[var(--teal)] text-[var(--text)]";
                return (
                  <button
                    key={oi}
                    type="button"
                    disabled={checked}
                    onClick={() => setPicks((p) => ({ ...p, [qi]: oi }))}
                    className={`rounded-[0.4rem] border px-[0.6rem] py-[0.2rem] text-left disabled:cursor-default ${cls}`}
                  >
                    {opt}
                    {checked && isCorrect ? "  ✓" : ""}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
        {!checked ? (
          <button
            type="button"
            disabled={Object.keys(picks).length < quiz.length}
            onClick={() => setChecked(true)}
            className="self-start rounded-[0.5rem] border border-[color-mix(in_srgb,var(--teal)_45%,var(--line))] bg-[color-mix(in_srgb,var(--teal)_14%,var(--panel-2))] px-[0.8rem] py-[0.3rem] text-[var(--text)] hover:brightness-[1.08] disabled:cursor-not-allowed disabled:opacity-45"
          >
            Check answers
          </button>
        ) : (
          <span className="text-[var(--muted)]">You got {score} / {quiz.length}.</span>
        )}
      </div>
    </details>
  );
}

/** Why an answer was unhelpful — short, single-select reasons (kept close to what users recognise). */
const DOWN_REASONS = [
  "Visual is unhelpful or broken",
  "Incorrect or incomplete",
  "Not what I asked for",
  "Slow or buggy",
  "Style or tone",
  "Safety or legal concern",
  "Other",
];

/** Markdown → speakable plain text: drop code blocks, links, images and markup so "Read aloud" sounds
 *  natural instead of reading `#`, `*` and URLs. */
function speakable(md: string): string {
  return (md || "")
    .replace(/```[\s\S]*?```/g, ". code block. ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^[#>\s-]*\|.*\|$/gm, " ")          // table rows
    .replace(/[#>*_~`|]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

const Icon = {
  copy: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  ),
  speaker: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" /><path d="M15.54 8.46a5 5 0 0 1 0 7.07" /><path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
    </svg>
  ),
  stop: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>
  ),
  share: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8" /><polyline points="16 6 12 2 8 6" /><line x1="12" y1="2" x2="12" y2="15" />
    </svg>
  ),
  retry: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="23 4 23 10 17 10" /><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
    </svg>
  ),
};

/** One icon button in the answer action bar. */
function ActionIcon({
  title, onClick, active, disabled, children,
}: { title: string; onClick: () => void; active?: boolean; disabled?: boolean; children: ReactNode }) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      aria-pressed={active}
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex h-[1.9rem] w-[1.9rem] items-center justify-center rounded-[0.4rem] hover:bg-[var(--panel-2)] hover:text-[var(--text)] disabled:opacity-40 ${
        active ? "bg-[var(--panel-2)] text-[var(--text)]" : "text-[var(--muted)]"
      }`}
    >
      {children}
    </button>
  );
}

/** The action bar under an answer: Copy · 👍 · 👎 · Read aloud · Share · Try again. 👍 is fire-and-forget;
 *  👎 records at once, then opens a panel to capture the reason (stored as the feedback note) and offers
 *  "Try again, better" to regenerate steered by that reason (feedback phase 2). Read aloud uses the
 *  browser's SpeechSynthesis (local, no cloud); Share uses the Web Share API, falling back to copy. */
export function AnswerActions({
  text,
  jobId,
  onRetry,
  onRegenerate,
}: {
  text: string;
  jobId?: string | null;
  onRetry?: () => void;
  onRegenerate?: (reason: string) => void;
}) {
  const [rating, setRating] = useState<"up" | "down" | null>(null);
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<string | null>(null);
  const [details, setDetails] = useState("");
  const [sent, setSent] = useState(false);
  const [copied, setCopied] = useState(false);
  const [speaking, setSpeaking] = useState(false);

  // stop any speech if the answer unmounts (chat switch, new turn) so it doesn't keep talking
  useEffect(() => () => {
    try { window.speechSynthesis?.cancel(); } catch { /* unsupported */ }
  }, []);

  const noteText = () => {
    const r = reason || "";
    const d = details.trim();
    return d ? (r ? `${r} — ${d}` : d) : r;
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard blocked */ }
  };
  const readAloud = () => {
    const synth = typeof window !== "undefined" ? window.speechSynthesis : undefined;
    if (!synth) return;
    if (speaking) { synth.cancel(); setSpeaking(false); return; }
    const u = new SpeechSynthesisUtterance(speakable(text).slice(0, 4000));
    u.onend = () => setSpeaking(false);
    u.onerror = () => setSpeaking(false);
    synth.cancel();
    synth.speak(u);
    setSpeaking(true);
  };
  const share = async () => {
    try {
      if (typeof navigator !== "undefined" && navigator.share) {
        await navigator.share({ text });
      } else {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }
    } catch { /* user cancelled the share sheet */ }
  };
  const up = () => {
    setRating("up");
    setOpen(false);
    if (jobId) void sendFeedback(jobId, "up").catch(() => undefined);
  };
  const down = () => {
    setRating("down");
    setSent(false);
    setOpen(true);
    // record the 👎 right away, so the signal isn't lost if the panel is dismissed without a reason
    if (jobId) void sendFeedback(jobId, "down").catch(() => undefined);
  };
  const store = () => {
    const note = noteText();
    if (jobId && note) void sendFeedback(jobId, "down", note).catch(() => undefined);
    setSent(true);
    setOpen(false);
  };
  const tryAgainBetter = () => {
    store();
    onRegenerate?.(noteText() || reason || "not helpful");
  };
  const canAct = Boolean(reason) || details.trim().length > 0;

  return (
    <div className="mt-[0.4rem]">
      <div className="flex items-center gap-[0.15rem]">
        <ActionIcon title={copied ? "Copied" : "Copy response"} onClick={copy} active={copied}>
          {Icon.copy}
        </ActionIcon>
        {jobId ? (
          <>
            <ActionIcon title="Good response" onClick={up} active={rating === "up"}>👍</ActionIcon>
            <ActionIcon title="Needs work" onClick={down} active={rating === "down"}>👎</ActionIcon>
          </>
        ) : null}
        <ActionIcon title={speaking ? "Stop" : "Read aloud"} onClick={readAloud} active={speaking}>
          {speaking ? Icon.stop : Icon.speaker}
        </ActionIcon>
        <ActionIcon title="Share" onClick={share}>{Icon.share}</ActionIcon>
        {onRetry ? <ActionIcon title="Try again" onClick={onRetry}>{Icon.retry}</ActionIcon> : null}
        {rating === "up" || sent ? (
          <span className="ml-[0.3rem] text-[0.72rem] text-[var(--muted)]">Thanks — noted.</span>
        ) : null}
      </div>
      {open ? (
        <div className="mt-[0.5rem] max-w-[32rem] rounded-[0.6rem] border border-[var(--line)] bg-[var(--panel-2)] p-[0.7rem]">
          <div className="mb-[0.5rem] text-[0.8rem] font-medium text-[var(--text)]">What was off?</div>
          <div className="flex flex-wrap gap-[0.35rem]">
            {DOWN_REASONS.map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => setReason((cur) => (cur === r ? null : r))}
                aria-pressed={reason === r}
                className={`rounded-full border px-[0.7rem] py-[0.28rem] text-[0.76rem] ${
                  reason === r
                    ? "border-[var(--teal)] bg-[var(--panel)] text-[var(--text)]"
                    : "border-[var(--line)] text-[var(--muted)] hover:text-[var(--text)]"
                }`}
              >
                {r}
              </button>
            ))}
          </div>
          <textarea
            value={details}
            onChange={(e) => setDetails(e.target.value)}
            rows={2}
            placeholder="Share details (optional)"
            className="mt-[0.5rem] w-full resize-y rounded-[0.5rem] border border-[var(--line)] bg-[var(--panel)] p-[0.5rem] text-[0.8rem] text-[var(--text)] placeholder:text-[var(--muted)]"
          />
          <div className="mt-[0.5rem] flex items-center justify-end gap-[0.35rem]">
            <button type="button" className="msg-action" onClick={() => setOpen(false)}>
              Dismiss
            </button>
            <button type="button" className="msg-action" onClick={store}>
              Submit
            </button>
            {onRegenerate ? (
              <button
                type="button"
                className="msg-action !border-[var(--teal)] !text-[var(--text)] disabled:opacity-50"
                onClick={tryAgainBetter}
                disabled={!canAct}
                title={canAct ? "Regenerate addressing your reason" : "Pick a reason first"}
              >
                Try again, better
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** Follow-up suggestions under an answer: click a chip to ask that next. */
export function SuggestionChips({ item, onPick }: { item: FeedItem; onPick?: (q: string) => void }) {
  const sugg = item.suggestions || [];
  if (item.pending || !sugg.length) return null;
  return (
    <div className="mt-[0.6rem] flex flex-wrap gap-[0.4rem]">
      {sugg.map((s, i) => (
        <button
          key={i}
          type="button"
          onClick={() => onPick?.(s)}
          className="rounded-full border border-[var(--line)] bg-[var(--panel-2)] px-[0.7rem] py-[0.3rem] text-[0.78rem] text-[var(--muted)] hover:border-[var(--teal)] hover:text-[var(--text)]"
        >
          {s}
        </button>
      ))}
    </div>
  );
}

/** The Options toggle's candidate approaches: click a card to get the full answer that way. */
export function OptionCards({ item, onPick }: { item: FeedItem; onPick?: (query: string, card: AnswerOption) => void }) {
  const opts = item.options || [];
  if (item.pending || !opts.length) return null;
  const query = item.optionsQuery || "";
  return (
    <div className="mt-[0.5rem] flex flex-col gap-[0.4rem]">
      {opts.map((o, i) => (
        <button
          key={i}
          type="button"
          onClick={() => onPick?.(query, o)}
          className="flex flex-col items-start gap-[0.1rem] rounded-[0.6rem] border border-[var(--line)] bg-[var(--panel-2)] px-[0.7rem] py-[0.5rem] text-left hover:border-[var(--teal)] hover:bg-[color-mix(in_srgb,var(--teal)_8%,var(--panel-2))]"
        >
          <span className="text-[0.85rem] font-semibold text-[var(--text)]">{o.title}</span>
          {o.hint ? <span className="text-[0.76rem] text-[var(--muted)]">{o.hint}</span> : null}
        </button>
      ))}
    </div>
  );
}

/** Images an answer produced; loaded from the job's files when the event only named them. */
export function AgentThumbs({ item, fallbackJobId }: ItemProps) {
  const jobId = item.jobId || fallbackJobId;
  const own = item.images && item.images.length > 0 ? item.images : null;
  const [loaded, setLoaded] = useState<ChatImage[]>([]);

  useEffect(() => {
    const files = item.imageFiles;
    if (own || !files?.length || !jobId) return;
    let cancelled = false;
    void (async () => {
      const out: ChatImage[] = [];
      for (const file of files) {
        if (!file.filename) continue;
        try {
          const url = await fetchJobAttachment(jobId, file.filename);
          out.push({ url, filename: file.filename, mime: file.mime || "image/png" });
        } catch {
          // skip
        }
      }
      if (!cancelled) setLoaded(out);
    })();
    return () => {
      cancelled = true;
    };
  }, [jobId, own, item.imageFiles]);

  const imgs = own || loaded;
  if (!imgs.length) return null;
  return (
    <div className="mb-[0.45rem] flex flex-wrap items-start gap-[0.4rem]">
      {imgs.map((img) => (
        <a
          key={img.url}
          href={img.url}
          target="_blank"
          rel="noreferrer"
          className="relative block max-h-[min(70vh,36rem)] max-w-[min(36rem,100%)] overflow-hidden rounded-[0.45rem] border border-[var(--line)] bg-[rgba(0,0,0,0.25)]"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={img.url}
            alt={img.filename}
            className="block h-auto max-h-[min(70vh,36rem)] w-auto max-w-full object-contain"
          />
        </a>
      ))}
    </div>
  );
}

/** Load the web images an answer found (from the job's attachment store) into blob URLs. Returns []
 *  until they resolve; one that fails to load is skipped. Shared by the spread-in-answer renderer and
 *  the fallback strip. */
export function useWebImages(item: FeedItem, fallbackJobId: string | null): ChatImage[] {
  const jobId = item.jobId || fallbackJobId;
  const [loaded, setLoaded] = useState<ChatImage[]>([]);
  useEffect(() => {
    const files = item.webImages || [];
    let cancelled = false;
    // resolve inside the async callback (never setState synchronously in the effect body); an empty
    // list just yields [] on the next tick, which clears a previous answer's images.
    void (async () => {
      const out: ChatImage[] = [];
      if (files.length && jobId) {
        for (const f of files) {
          if (!f.filename) continue;
          try {
            const url = await fetchJobAttachment(jobId, f.filename);
            out.push({ url, filename: f.filename, mime: f.mime || "image/jpeg" });
          } catch {
            // skip one that won't load
          }
        }
      }
      if (!cancelled) setLoaded(out);
    })();
    return () => {
      cancelled = true;
    };
  }, [jobId, item.webImages]);
  return loaded;
}

/** One web image at its natural size (not a cropped square), capped so it never dominates; click to
 *  open full size. `align` lets callers alternate left/right as images are spread down the answer. */
export function SpreadImage({ img, align = "left" }: { img: ChatImage; align?: "left" | "right" }) {
  return (
    <a
      href={img.url}
      target="_blank"
      rel="noreferrer"
      title="Open image"
      className={`my-[0.6rem] block w-fit max-w-full overflow-hidden rounded-[0.5rem] border border-[var(--line)] bg-[rgba(0,0,0,0.25)] hover:border-[var(--teal)] ${
        align === "right" ? "ml-auto" : ""
      }`}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={img.url}
        alt={img.filename}
        loading="lazy"
        className="block h-auto max-h-[15rem] w-auto max-w-full object-contain"
      />
    </a>
  );
}

/** Fallback when an answer has no section gaps to spread images into: a row of varied-size thumbnails
 *  (each keeps its own aspect ratio at a fixed height), click to enlarge. */
export function WebImages({ item, fallbackJobId }: ItemProps) {
  const loaded = useWebImages(item, fallbackJobId);
  if (!loaded.length) return null;
  return (
    <div className="mt-[0.5rem] flex flex-wrap items-start gap-[0.5rem]">
      {loaded.map((img) => (
        <a
          key={img.url}
          href={img.url}
          target="_blank"
          rel="noreferrer"
          title="Open image"
          className="block h-[7rem] overflow-hidden rounded-[0.45rem] border border-[var(--line)] bg-[rgba(0,0,0,0.25)] hover:border-[var(--teal)]"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={img.url} alt={img.filename} loading="lazy" className="block h-full w-auto object-contain" />
        </a>
      ))}
    </div>
  );
}

/** Download buttons for the files a job wrote (documents, scan tables). */
export function ArtifactChips({ item, fallbackJobId }: ItemProps) {
  const jobId = item.jobId || fallbackJobId;
  const arts = item.artifacts || [];
  if (!arts.length || !jobId) return null;

  async function download(filename: string) {
    try {
      const url = await fetchJobAttachment(jobId as string, filename);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1500);
    } catch (err) {
      alert(errorText(err));
    }
  }

  return (
    <div className="mb-[0.45rem] flex flex-wrap gap-[0.35rem]">
      {arts.map((art) => (
        <button
          key={art.filename}
          type="button"
          className="cursor-pointer rounded-full border border-[color-mix(in_srgb,var(--teal)_45%,var(--line))] bg-[color-mix(in_srgb,var(--teal)_12%,var(--panel-2))] px-[0.65rem] py-[0.25rem] text-[0.75rem] text-[var(--text)] hover:brightness-[1.08]"
          title={`Download ${art.filename}`}
          onClick={() => void download(art.filename)}
        >
          ↓ {art.filename}
          {art.bytes ? ` · ${Math.max(1, Math.round(art.bytes / 1024))} KB` : ""}
        </button>
      ))}
    </div>
  );
}

function ClaimList({ title, claims }: { title: string; claims: { text: string; cites: number[] }[] }) {
  if (!claims.length) return null;
  return (
    <>
      <p className="mt-[0.45rem] mb-[0.2rem] font-semibold">{title}</p>
      <ul className="m-0 pl-[1.1rem]">
        {claims.map((c, i) => (
          <li key={i} className="my-[0.15rem] text-[var(--text)]">
            {c.text} <span className="text-[var(--muted)]">[{c.cites.join(", ")}]</span>
          </li>
        ))}
      </ul>
    </>
  );
}

/** The check after a knowledge-base answer: how many cited statements the sources confirm, and which not. */
export function VerificationNote({ item }: { item: FeedItem }) {
  const v = item.verification;
  if (item.pending || !v || !v.checked) return null;
  const allGood = v.supported === v.checked;
  const problems = v.unsupported.length;
  const label = allGood
    ? `✓ All ${v.checked} statements confirmed by the sources`
    : `${problems ? "⚠" : "✓"} ${v.supported} of ${v.checked} statements confirmed by the sources` +
      (problems ? ` · ${problems} not supported` : "");
  const box = "mt-[0.6rem] mb-[0.2rem] rounded-[8px] px-[0.7rem] py-[0.4rem] text-[0.78rem] text-[var(--muted)]";
  const okBorder = "border border-[color-mix(in_srgb,var(--teal)_35%,var(--line))]";
  const warnBox =
    "border border-[color-mix(in_srgb,var(--danger)_45%,var(--line))] bg-[color-mix(in_srgb,var(--danger)_7%,var(--panel-2))]";
  if (allGood) return <div className={`${box} ${okBorder}`}>{label}</div>;
  return (
    <details className={`${box} ${problems ? warnBox : okBorder}`} open={problems > 0}>
      <summary className={`cursor-pointer font-semibold ${problems ? "text-[var(--text)]" : ""}`}>{label}</summary>
      <ClaimList title="Not supported by the cited source — treat with care:" claims={v.unsupported} />
      <ClaimList title="Only partly confirmed:" claims={v.unclear} />
    </details>
  );
}

/** Knowledge-base sources under a grounded answer; numbers match the [n] citations in the text. */
function hostOf(url?: string | null): string {
  try {
    return url ? new URL(url).hostname.replace(/^www\./, "") : "";
  } catch {
    return "";
  }
}

function faviconOf(host: string): string {
  return host ? `https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=64` : "";
}

export function SourceList({ item }: { item: FeedItem }) {
  const sources = item.sources || [];
  if (item.pending || !item.grounded || !sources.length) return null;
  const cited = sources.filter((s) => s.cited);
  const shown = cited.length ? cited : sources;
  return (
    <details
      className="mt-[0.6rem] mb-[0.2rem] rounded-[8px] border border-[color-mix(in_srgb,var(--teal)_35%,var(--line))] bg-[color-mix(in_srgb,var(--teal)_6%,var(--panel-2))] px-[0.7rem] py-[0.45rem] text-[0.78rem]"
      open
    >
      <summary className="cursor-pointer font-semibold text-[var(--muted)]">
        Sources · {shown.length}
        {cited.length && cited.length < sources.length ? ` cited (${sources.length} found)` : ""}
      </summary>
      <table className="mt-[0.45rem] w-full border-collapse text-[0.78rem]">
        <tbody>
          {shown.map((s, idx) => {
            const host = hostOf(s.url);
            const ico = faviconOf(host);
            const linkable = s.url && /^https?:\/\//i.test(s.url);
            const td = `px-[0.5rem] py-[0.4rem] align-top ${
              idx === 0 ? "" : "border-t border-t-[color-mix(in_srgb,var(--line)_70%,transparent)]"
            }`;
            return (
              <tr key={s.n} className="hover:[&>td]:bg-[rgba(255,255,255,0.025)]">
                <td className={`${td} w-[1.4rem] text-[var(--muted)] [font-variant-numeric:tabular-nums]`}>{s.n}</td>
                <td className={td}>
                  {linkable ? (
                    <a
                      href={s.url as string}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="group inline-flex items-center gap-[0.4rem] text-[var(--teal)] no-underline"
                    >
                      {ico ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={ico} alt="" className="h-4 w-4 shrink-0 rounded-[3px]" loading="lazy" />
                      ) : null}
                      <span className="min-w-0 group-hover:underline">{s.title}</span>
                    </a>
                  ) : (
                    <span className="min-w-0">{s.title}</span>
                  )}
                  {s.heading_path ? (
                    <span className="text-[var(--text)]"> › {s.heading_path.split(" > ").pop()}</span>
                  ) : null}
                </td>
                <td className={`${td} whitespace-nowrap text-[var(--muted)]`}>{host || s.publisher || ""}</td>
                <td className={`${td} whitespace-nowrap text-right text-[var(--muted)]`}>
                  {s.refreshing
                    ? "re-checking…"
                    : s.updated_at
                      ? s.updated_at
                      : s.checked_at
                        ? timeAgo(s.checked_at)
                        : ""}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </details>
  );
}

/** The tools an agentic step used, collapsed. */
export function ToolDrawer({ item }: { item: FeedItem }) {
  const tools = item.tools || [];
  if (!tools.length) return null;
  return (
    <details className="mt-[0.35rem] border-t border-t-[var(--line)] pt-[0.3rem] text-[0.72rem] text-[var(--muted)]">
      <summary className="cursor-pointer select-none font-medium text-[var(--teal)]">
        {tools.length} tool{tools.length === 1 ? "" : "s"} used
      </summary>
      <ul className="m-0 mt-[0.45rem] flex list-none flex-col gap-[0.45rem] p-0">
        {tools.map((step) => (
          <li
            key={step.id}
            className={`rounded-[0.55rem] border bg-[rgba(0,0,0,0.18)] px-[0.55rem] py-[0.45rem] ${
              step.error ? "border-[color-mix(in_srgb,var(--danger)_45%,var(--line))]" : "border-[var(--line)]"
            }`}
          >
            <strong className="mb-[0.15rem] block text-[var(--text)]">{step.tool}</strong>
            {step.args ? <span className="mb-[0.25rem] block text-[var(--muted)]">{step.args}</span> : null}
            {step.result ? (
              <pre className="m-0 max-h-[10rem] overflow-auto whitespace-pre-wrap break-words text-[0.72rem] leading-[1.4] text-[#d7d2c8] [font-family:var(--font-mono)]">
                {step.result}
              </pre>
            ) : (
              <em className="mb-[0.25rem] block text-[var(--muted)]">running…</em>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}
