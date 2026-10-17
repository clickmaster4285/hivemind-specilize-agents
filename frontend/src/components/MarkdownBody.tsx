"use client";

import { isValidElement, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";

import { getApiOrigin } from "@/lib/api";

import MermaidDiagram from "./MermaidDiagram";

/** Only our own images load inside answers. A model once wrote an image.pollinations.ai link with
 *  the user's prompt in it: showing it would have sent the prompt to a cloud service. */
function isOwnImage(src: string): boolean {
  if (src.startsWith("/") && !src.startsWith("//")) return true;
  try {
    const origin = new URL(src).origin;
    return origin === window.location.origin || origin === new URL(getApiOrigin()).origin;
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
    <a href={src} target="_blank" rel="noreferrer nofollow" className="md-a md-external-img"
      title="Not loaded automatically: opening it contacts another website">
      [external image not loaded: {alt || "image"} — {host}]
    </a>
  );
}

/** The code of a ```mermaid block, or null for any other <pre>. */
function mermaidCode(children: ReactNode): string | null {
  if (!isValidElement<{ className?: string; children?: ReactNode }>(children)) return null;
  if (!children.props.className?.includes("language-mermaid")) return null;
  return String(children.props.children ?? "").trim();
}

type Props = {
  content: string;
  pending?: boolean;
};

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

export default function MarkdownBody({ content, pending }: Props) {
  if (pending) {
    return <div className="bubble-body pending-text">{content}</div>;
  }

  return (
    <div className="md-body">
      <ReactMarkdown
        components={{
          h1: ({ children }) => <h3 className="md-h">{children}</h3>,
          h2: ({ children }) => <h3 className="md-h">{children}</h3>,
          h3: ({ children }) => <h4 className="md-h">{children}</h4>,
          h4: ({ children }) => <h4 className="md-h">{children}</h4>,
          p: ({ children }) => <p className="md-p">{children}</p>,
          ul: ({ children }) => <ul className="md-ul">{children}</ul>,
          ol: ({ children }) => <ol className="md-ol">{children}</ol>,
          li: ({ children }) => <li className="md-li">{children}</li>,
          strong: ({ children }) => <strong className="md-strong">{children}</strong>,
          em: ({ children }) => <em>{children}</em>,
          // keep className: <pre> needs "language-mermaid" to spot diagrams
          code: ({ className, children }) => <code className={`md-code ${className ?? ""}`.trim()}>{children}</code>,
          pre: ({ children }) => {
            const mermaid = mermaidCode(children);
            return mermaid !== null ? <MermaidDiagram code={mermaid} /> : <pre className="md-pre">{children}</pre>;
          },
          img: ({ src, alt }) => {
            const url = typeof src === "string" ? src : "";
            // eslint-disable-next-line @next/next/no-img-element -- answers hold arbitrary sizes
            return isOwnImage(url) ? <img src={url} alt={alt ?? ""} className="md-img" /> : <ExternalImage src={url} alt={alt ?? ""} />;
          },
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer" className="md-a">
              {children}
            </a>
          ),
        }}
      >
        {normalizeMarkdown(content)}
      </ReactMarkdown>
    </div>
  );
}
