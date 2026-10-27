"use client";

/** The parts shown under a message: generated images, downloads, the source check, sources, tools. */
import { useEffect, useState } from "react";

import { fetchJobAttachment } from "@/lib/api";
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
