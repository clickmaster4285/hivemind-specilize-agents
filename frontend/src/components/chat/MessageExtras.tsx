"use client";

/** The parts shown under a message: generated images, downloads, the source check, sources, tools. */
import { useEffect, useState } from "react";

import { fetchJobAttachment } from "@/lib/api";
import { errorText, timeAgo } from "@/lib/chat";
import type { ChatImage, FeedItem } from "@/lib/types";

type ItemProps = { item: FeedItem; fallbackJobId: string | null };

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
    <div className="chat-thumbs chat-thumbs-generated">
      {imgs.map((img) => (
        <a key={img.url} href={img.url} target="_blank" rel="noreferrer">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={img.url} alt={img.filename} />
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
      <p className="answer-check-head">{title}</p>
      <ul>
        {claims.map((c, i) => (
          <li key={i}>
            {c.text} <span className="answer-check-cites">[{c.cites.join(", ")}]</span>
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
  if (allGood) return <div className="answer-check ok">{label}</div>;
  return (
    <details className={`answer-check ${problems ? "warn" : "ok"}`} open={problems > 0}>
      <summary>{label}</summary>
      <ClaimList title="Not supported by the cited source — treat with care:" claims={v.unsupported} />
      <ClaimList title="Only partly confirmed:" claims={v.unclear} />
    </details>
  );
}

/** Knowledge-base sources under a grounded answer; numbers match the [n] citations in the text. */
export function SourceList({ item }: { item: FeedItem }) {
  const sources = item.sources || [];
  if (item.pending || !item.grounded || !sources.length) return null;
  const cited = sources.filter((s) => s.cited);
  const shown = cited.length ? cited : sources;
  return (
    <details className="brain-sources" open>
      <summary>
        Sources · {shown.length}
        {cited.length && cited.length < sources.length ? ` cited (${sources.length} found)` : ""}
      </summary>
      <ol>
        {shown.map((s) => (
          <li key={s.n} value={s.n}>
            {s.url && /^https?:\/\//i.test(s.url) ? (
              <a href={s.url} target="_blank" rel="noreferrer noopener">
                {s.title}
              </a>
            ) : (
              <span>{s.title}</span>
            )}
            {s.heading_path ? (
              <span className="brain-source-section"> › {s.heading_path.split(" > ").pop()}</span>
            ) : null}
            <span className="brain-source-meta">
              {[
                s.publisher,
                s.updated_at ? `updated ${s.updated_at}` : null,
                s.refreshing ? "re-checking for changes now" : s.checked_at ? `checked ${timeAgo(s.checked_at)}` : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </li>
        ))}
      </ol>
    </details>
  );
}

/** The tools an agentic step used, collapsed. */
export function ToolDrawer({ item }: { item: FeedItem }) {
  const tools = item.tools || [];
  if (!tools.length) return null;
  return (
    <details className="tool-drawer">
      <summary>
        {tools.length} tool{tools.length === 1 ? "" : "s"} used
      </summary>
      <ul>
        {tools.map((step) => (
          <li key={step.id} className={step.error ? "bad" : ""}>
            <strong>{step.tool}</strong>
            {step.args ? <span>{step.args}</span> : null}
            {step.result ? <pre>{step.result}</pre> : <em>running…</em>}
          </li>
        ))}
      </ul>
    </details>
  );
}
