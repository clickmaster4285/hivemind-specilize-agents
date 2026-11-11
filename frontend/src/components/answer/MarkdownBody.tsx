"use client";

import { isValidElement, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
// tables, strikethrough, task lists: without it a | table | shows as raw text
import remarkGfm from "remark-gfm";

import MermaidDiagram from "@/components/answer/MermaidDiagram";
import NetworkDiagram from "@/components/answer/NetworkDiagram";
import { mdPre } from "@/lib/ui";

/** Answer typography: a reading size and leading for long answers; headings in the body face (Syne stays for
 *  branding — a display face at 15–19px reads poorly) with a clear size/weight scale and room above them.
 *  Nested lists change marker; even table rows are striped; inline-code styling is dropped inside a <pre>. */
const HEAD = "leading-[1.35] first:mt-0 [font-family:var(--font-body)]";
const HEAD_STRONG = `${HEAD} tracking-[-0.005em] text-text-strong`;
const CELL = "whitespace-nowrap border-b border-line px-[0.6rem] py-[0.3rem] text-left align-top";
const md = {
  body: "text-[0.97rem] leading-[1.7] text-text [overflow-wrap:anywhere] [&>:first-child]:mt-0",
  h1: `${HEAD_STRONG} mt-[1.6rem] mb-[0.65rem] border-b border-line pb-[0.35rem] text-[1.3rem] font-bold`,
  h2: `${HEAD_STRONG} mt-[1.45rem] mb-2 text-[1.16rem] font-semibold`,
  h3: `${HEAD_STRONG} mt-[1.25rem] mb-[0.4rem] text-[1.05rem] font-semibold`,
  h4: `${HEAD} mt-4 mb-[0.35rem] text-[0.8rem] font-semibold uppercase tracking-[0.05em] text-muted`,
  p: "mb-[0.8rem]",
  ul: "mt-[0.4rem] mb-[0.85rem] list-disc pl-[1.45rem] [ol_&]:my-[0.3rem] [ol_&]:list-[circle] [ul_&]:my-[0.3rem] [ul_&]:list-[circle]",
  ol: "mt-[0.4rem] mb-[0.85rem] list-decimal pl-[1.45rem] [ol_&]:my-[0.3rem] [ol_&]:list-[lower-alpha]",
  li: "my-[0.35rem] pl-[0.15rem] leading-[1.6] marker:text-copper",
  strong: "font-semibold text-text-strong",
  code: "rounded-[0.35rem] bg-code-bg px-[0.4rem] py-[0.12rem] text-[0.84em] text-code-text [font-family:var(--font-mono)] [pre_&]:bg-transparent [pre_&]:p-0 [pre_&]:text-inherit [pre_&]:[font-size:inherit]",
  tableWrap: "mt-[0.4rem] mb-[0.6rem] max-w-full overflow-x-auto rounded-[0.45rem] border border-line",
  table: "w-max min-w-full border-collapse text-[0.8rem]",
  th: `${CELL} sticky top-0 bg-panel-2 font-semibold text-text`,
  td: `${CELL} [tr:nth-child(even)_&]:bg-row-alt [tbody_tr:hover_&]:bg-copper-soft`,
  img: "max-w-full rounded-[0.45rem]",
  a: "text-teal",
  externalImg: "text-[0.82em] text-muted",
};

// Links and images in answers (L16 S4): http(s), mailto and relative only. react-markdown's own filter already
// drops javascript:, data: and the rest; it also lets chat schemes through, which an answer never needs
const CHAT_SCHEMES = /^(ircs?|xmpp):/i;
const answerUrl = (url: string) => {
  const safe = defaultUrlTransform(url);
  return CHAT_SCHEMES.test(safe) ? "" : safe;
};

/** Only our own images load inside answers. A model once wrote an image.pollinations.ai link with
 *  the user's prompt in it: showing it would have sent the prompt to a cloud service. */
function isOwnImage(src: string): boolean {
  if (src.startsWith("/") && !src.startsWith("//")) return true;
  try {
    return new URL(src).origin === window.location.origin;     // the API is served from the page's origin
  } catch {
    return false;
  }
}

function ExternalImage({ src, alt }: { src: string; alt: string }) {
  let host = src;
  try {
    host = new URL(src).host;
  } catch {
    /* keep the raw text */
  }
  return (
    <a href={src} target="_blank" rel="noreferrer nofollow" className={md.externalImg}
      title="Not loaded automatically: opening it contacts another website">
      [external image not loaded: {alt || "image"} — {host}]
    </a>
  );
}

/** The code of a fenced block whose language tag matches `lang`, or null for any other <pre>. */
function fencedCode(children: ReactNode, lang: string): string | null {
  if (!isValidElement<{ className?: string; children?: ReactNode }>(children)) return null;
  if (!children.props.className?.includes(`language-${lang}`)) return null;
  return String(children.props.children ?? "").trim();
}

type Props = {
  content: string;
  pending?: boolean;
  sources?: CiteSrc[];
};

type CiteSrc = { n: number; url?: string | null; title?: string; publisher?: string | null; updated_at?: string | null };

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function faviconOf(host: string): string {
  return host ? `https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=64` : "";
}

/** One inline citation group: a pill with the first source (favicon + name) and "+N" for the rest;
 *  hovering opens a card listing every source in the group, each linking out. */
function CiteCluster({ sources }: { sources: CiteSrc[] }) {
  const [card, setCard] = useState<{ x: number; y: number } | null>(null);
  const closeTimer = useRef<number | null>(null);

  const open = (el: HTMLElement) => {
    if (closeTimer.current) window.clearTimeout(closeTimer.current);
    const r = el.getBoundingClientRect();
    setCard({ x: r.left + r.width / 2, y: r.top });
  };
  const scheduleClose = () => {
    closeTimer.current = window.setTimeout(() => setCard(null), 160);
  };
  const keepOpen = () => {
    if (closeTimer.current) window.clearTimeout(closeTimer.current);
  };

  if (!sources.length) return null;
  const first = sources[0];
  const host = hostOf(first.url || "");
  const name = first.publisher || host || "source";
  const ico = faviconOf(host);
  const extra = sources.length - 1;

  return (
    <span className="whitespace-nowrap">
      <a
        href={first.url ?? undefined}
        target="_blank"
        rel="noreferrer noopener"
        className="relative top-[-0.06rem] mx-[0.12rem] inline-flex max-w-48 items-center gap-[0.3rem] overflow-hidden rounded-full border border-[color-mix(in_srgb,var(--teal)_30%,var(--line))] bg-[color-mix(in_srgb,var(--teal)_10%,var(--panel-2))] px-[0.42rem] py-[0.08rem] align-middle leading-[1.5] no-underline transition-[transform,border-color] duration-100 hover:-translate-y-px hover:border-teal"
        title={name}
        onMouseEnter={(e) => open(e.currentTarget)}
        onMouseLeave={scheduleClose}
      >
        {ico ? <img src={ico} alt="" className="h-[0.85rem] w-[0.85rem] shrink-0 rounded-[2px]" loading="lazy" /> : null}
        <span className="truncate text-[0.72rem] text-text">{name}</span>
        {extra > 0 ? <span className="shrink-0 text-[0.68rem] font-semibold text-teal">+{extra}</span> : null}
      </a>
      {card
        ? createPortal(
            <div
              className="pointer-events-auto fixed z-[60] max-h-72 w-[19rem] max-w-[85vw] -translate-x-1/2 -translate-y-full overflow-y-auto rounded-[0.7rem] border border-line bg-panel p-[0.3rem] shadow-shadow [font-family:var(--font-body)]"
              style={{ left: card.x, top: card.y - 6 }}
              role="tooltip"
              onMouseEnter={keepOpen}
              onMouseLeave={scheduleClose}
            >
              {sources.map((s) => {
                const h = hostOf(s.url || "");
                const sico = faviconOf(h);
                return (
                  <a
                    key={s.n}
                    href={s.url ?? undefined}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="flex items-start gap-2 rounded-[0.45rem] px-[0.45rem] py-[0.4rem] no-underline hover:bg-[color-mix(in_srgb,var(--teal)_10%,transparent)]"
                  >
                    {sico ? <img src={sico} alt="" className="mt-[0.1rem] h-4 w-4 shrink-0 rounded-[3px]" loading="lazy" /> : null}
                    <span className="min-w-0 flex-1">
                      <span className="block text-[0.66rem] text-muted">{s.publisher || h}</span>
                      <span className="block overflow-hidden text-[0.78rem] leading-[1.25] text-text [-webkit-box-orient:vertical] [-webkit-line-clamp:2] [display:-webkit-box]">
                        {s.title ?? h}
                      </span>
                    </span>
                  </a>
                );
              })}
            </div>,
            document.body,
          )
        : null}
    </span>
  );
}

/** Turn a run of inline [n] citation markers into one link (title "cite:1,2,3") so a run like [1][2]
 *  renders as a single source chip with a "+N" overflow, instead of a row of tiny icons or an end-only list. */
function linkCitations(text: string, sources?: Array<{ n: number; url?: string | null }>): string {
  if (!sources?.length) return text;
  const byN = new Map<number, string>();
  for (const s of sources) if (s.url && /^https?:\/\//i.test(s.url)) byN.set(s.n, s.url);
  if (!byN.size) return text;
  return text.replace(/(?:\[\d{1,3}\])+(?!\()/g, (run) => {
    const nums = (run.match(/\d{1,3}/g) || []).map(Number).filter((n) => byN.has(n));
    if (!nums.length) return run;
    return `[${nums.join(",")}](${byN.get(nums[0])} "cite:${nums.join(",")}")`;
  });
}

/** Normalize model quirks so markdown renders cleanly. */
function normalizeMarkdown(raw: string): string {
  let text = raw.replace(/\r\n/g, "\n").trim();

  // Bullet glyphs some local models emit instead of "-"
  text = text.replace(/^[•●▪◦]\s+/gm, "- ");
  // "� **" leftovers / odd replacement chars before bold bullets
  text = text.replace(/^[\uFFFD?]+\s*/gm, "");
  // Ensure a blank line before headings so parsers treat them as headings
  text = text.replace(/([^\n])\n(#{1,4}\s)/g, "$1\n\n$2");
  // Ensure list items are on their own lines after a colon-ended intro
  text = text.replace(/:\s*\n([-*])/g, ":\n\n$1");

  return text;
}

export default function MarkdownBody({ content, pending, sources }: Props) {
  if (pending) {
    return (
      <div className="whitespace-pre-wrap text-[0.74rem] leading-[1.35] text-muted [font-family:var(--font-mono)]">
        {content}
      </div>
    );
  }

  const srcByN = new Map((sources || []).map((s) => [s.n, s]));

  return (
    <div className={md.body}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={answerUrl}
        components={{
          // wide tables (network scan, weather) scroll sideways instead of squeezing the chat
          table: ({ children }) => (
            <div className={md.tableWrap}>
              <table className={md.table}>{children}</table>
            </div>
          ),
          th: ({ children }) => <th className={md.th}>{children}</th>,
          td: ({ children }) => <td className={md.td}>{children}</td>,
          h1: ({ children }) => <h2 className={md.h1}>{children}</h2>,
          h2: ({ children }) => <h3 className={md.h2}>{children}</h3>,
          h3: ({ children }) => <h4 className={md.h3}>{children}</h4>,
          h4: ({ children }) => <h5 className={md.h4}>{children}</h5>,
          p: ({ children }) => <p className={md.p}>{children}</p>,
          ul: ({ children }) => <ul className={md.ul}>{children}</ul>,
          ol: ({ children }) => <ol className={md.ol}>{children}</ol>,
          li: ({ children }) => <li className={md.li}>{children}</li>,
          strong: ({ children }) => <strong className={md.strong}>{children}</strong>,
          em: ({ children }) => <em>{children}</em>,
          // keep className: <pre> needs "language-mermaid" to spot diagrams
          code: ({ className, children }) => <code className={`${md.code} ${className ?? ""}`.trim()}>{children}</code>,
          pre: ({ children }) => {
            const mermaid = fencedCode(children, "mermaid");
            if (mermaid !== null) return <MermaidDiagram code={mermaid} />;
            const net = fencedCode(children, "netdiagram");
            if (net !== null) return <NetworkDiagram code={net} />;
            return <pre className={mdPre}>{children}</pre>;
          },
          img: ({ src, alt }) => {
            const url = typeof src === "string" ? src : "";
            // eslint-disable-next-line @next/next/no-img-element -- answers hold arbitrary sizes
            return isOwnImage(url) ? <img src={url} alt={alt ?? ""} className={md.img} /> : <ExternalImage src={url} alt={alt ?? ""} />;
          },
          a: ({ href, title, children }) => {
            if (title && title.startsWith("cite")) {
              const raw = title.includes(":") ? title.slice(title.indexOf(":") + 1) : "";
              const nums = (raw.match(/\d+/g) || []).map(Number);
              const group = nums.map((n) => srcByN.get(n)).filter((s): s is CiteSrc => !!s);
              if (group.length) return <CiteCluster sources={group} />;
            }
            return (
              <a href={href} target="_blank" rel="noreferrer" className={md.a}>
                {children}
              </a>
            );
          },
        }}
      >
        {linkCitations(normalizeMarkdown(content), sources)}
      </ReactMarkdown>
    </div>
  );
}
