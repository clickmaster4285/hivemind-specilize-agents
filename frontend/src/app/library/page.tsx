"use client";

import { useState } from "react";

import { useApp } from "@/components/app/AppProvider";
import FileRow from "@/components/app/FileRow";
import PageHeader, { pageSearch, pageTab } from "@/components/app/PageHeader";
import { useLibrary } from "@/hooks/useLibrary";
import { useOpenChat } from "@/hooks/useOpenChat";
import type { LibraryFile } from "@/lib/api";
import { LIBRARY_GROUPS, libraryGroup, type LibraryGroup } from "@/lib/library";

/** /library (BACKLOG L24 U5): every file the app made in the user's chats — documents, spreadsheets, slides,
 *  generated pictures — newest first, with search and a filter per group (only groups that have files). */
export default function LibraryPage() {
  const app = useApp();
  const { openChat } = useOpenChat();
  const { files, error } = useLibrary();
  const [search, setSearch] = useState("");
  const [group, setGroup] = useState<LibraryGroup | "All">("All");
  if (!app.auth.user) return null; // the shell shows the login when signed out

  const all = files || [];
  const present = LIBRARY_GROUPS.filter((g) => all.some((f) => libraryGroup(f) === g));
  const needle = search.trim().toLowerCase();
  const shown = all.filter(
    (f) => (group === "All" || libraryGroup(f) === group)
      && (!needle || `${f.filename} ${f.query}`.toLowerCase().includes(needle)),
  );
  const threadOf = (f: LibraryFile) => app.history.threads.find((t) => t.id === f.thread_id);

  return (
    <PageHeader
      title="Library"
      about="Files the assistant made in your chats: documents, spreadsheets, slides and pictures."
      toolbar={
        <>
          <input className={pageSearch} placeholder="Search files" value={search} onChange={(e) => setSearch(e.target.value)} />
          {present.length > 1 ? (
            <div role="tablist" aria-label="Show" className="flex flex-wrap gap-1 rounded-xl border border-line p-1">
              {(["All", ...present] as const).map((g) => (
                <button key={g} type="button" role="tab" aria-selected={group === g} className={pageTab(group === g)}
                        onClick={() => setGroup(g)}>
                  {g}
                </button>
              ))}
            </div>
          ) : null}
        </>
      }
    >
      {error ? <p className="mt-6 text-[0.9rem] text-danger-text">Could not load the library: {error}</p> : null}
      {files === null && !error ? <p className="mt-6 text-[0.9rem] text-muted">Loading…</p> : null}
      {files !== null && shown.length === 0 ? (
        <p className="mt-6 text-[0.9rem] text-muted">
          {all.length ? "No file matches." : "No files yet. Ask for a report, a spreadsheet or slides and they appear here."}
        </p>
      ) : null}
      <ul className="m-0 mt-4 list-none p-0">
        {shown.map((f) => {
          const thread = threadOf(f);
          return (
            <FileRow key={`${f.job_id}/${f.filename}`} file={f} onOpenChat={thread ? () => openChat(thread) : undefined} />
          );
        })}
      </ul>
    </PageHeader>
  );
}
