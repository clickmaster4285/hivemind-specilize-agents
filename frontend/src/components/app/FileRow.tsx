"use client";

import { useState } from "react";

import type { LibraryFile } from "@/lib/api";
import { errorText, timeAgo } from "@/lib/chat";
import { fileSize } from "@/lib/format";
import { downloadLibraryFile, libraryGroup } from "@/lib/library";
import { btn } from "@/lib/ui";

/** One made file (Library page, new-chat screen): name, the chat it came from, when, size; Download and, when the
 *  chat is still in the history, Open chat. */
export default function FileRow({ file, onOpenChat }: { file: LibraryFile; onOpenChat?: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const ext = file.filename.includes(".") ? file.filename.split(".").pop()!.toUpperCase() : libraryGroup(file);
  return (
    <li className="flex items-center gap-3 border-b border-line py-2.5">
      <span className="flex h-9 w-11 shrink-0 items-center justify-center rounded-md border border-line bg-panel-2 text-[0.62rem] font-semibold text-muted">
        {ext}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[0.9rem] text-text" title={file.filename}>{file.filename}</span>
        <span className="block truncate text-[0.74rem] text-muted" title={file.query}>
          {file.created_at ? timeAgo(file.created_at) : ""}
          {file.bytes ? ` · ${fileSize(file.bytes)}` : ""}
          {file.query ? ` · ${file.query}` : ""}
        </span>
        {error ? <span className="block text-[0.74rem] text-danger-text">{error}</span> : null}
      </span>
      <span className="flex shrink-0 gap-1.5">
        {onOpenChat ? (
          <button type="button" className={btn("ghost", "sm")} onClick={onOpenChat}>Open chat</button>
        ) : null}
        <button type="button" className={btn("sm")}
                onClick={() => downloadLibraryFile(file).then(() => setError(null), (e: unknown) => setError(errorText(e)))}>
          Download
        </button>
      </span>
    </li>
  );
}
