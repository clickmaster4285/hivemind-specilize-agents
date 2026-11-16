"use client";

import { useState, type ReactNode } from "react";

import { saveText } from "@/lib/download";
import { mdPre } from "@/lib/ui";

type Props = { code: string; language: string; children: ReactNode };

// A fence's language → the file extension of its download, where the two differ; any other short tag is used as
// it is (`json` → .json), and a missing or odd one gives .txt.
const EXTENSION: Record<string, string> = {
  bash: "sh", shell: "sh", zsh: "sh", console: "sh", python: "py", javascript: "js", typescript: "ts",
  powershell: "ps1", ps: "ps1", yaml: "yml", markdown: "md", text: "txt", plaintext: "txt",
};
const PLAIN_TAG = /^[a-z0-9]{1,8}$/;
// How long "Copied" shows after a click.
const COPIED_MS = 1500;

function extensionOf(language: string): string {
  return EXTENSION[language] ?? (PLAIN_TAG.test(language) ? language : "txt");
}

const barCls =
  "flex items-center justify-between gap-2 rounded-t-[0.55rem] border border-b-0 border-line bg-panel-2 " +
  "px-[0.9rem] py-[0.3rem] text-[0.72rem] text-muted";
const actionCls = "rounded px-[0.4rem] py-[0.1rem] hover:bg-hover hover:text-text";

/** A fenced code block in an answer, with a bar showing its language, Copy and Download (ISSUES_LOG O74). */
export default function CodeBlock({ code, language, children }: Props) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), COPIED_MS);
  };
  return (
    <div className="mt-2 mb-[0.8rem]">
      <div className={barCls}>
        <span className="[font-family:var(--font-mono)]">{language || "code"}</span>
        <span className="flex gap-1">
          <button type="button" className={actionCls} onClick={() => void copy()}>
            {copied ? "Copied" : "Copy"}
          </button>
          <button type="button" className={actionCls}
            onClick={() => saveText(code, `snippet.${extensionOf(language)}`, "text/plain")}>
            Download
          </button>
        </span>
      </div>
      <pre className={`${mdPre} mt-0! mb-0! rounded-t-none`}>{children}</pre>
    </div>
  );
}
