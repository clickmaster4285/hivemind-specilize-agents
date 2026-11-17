"use client";

import { useRouter } from "next/navigation";
import type { ReactNode } from "react";

import FileRow from "@/components/app/FileRow";
import { useLibrary } from "@/hooks/useLibrary";
import { useOpenChat } from "@/hooks/useOpenChat";
import type { ChatThread } from "@/lib/threads";
import { LIBRARY_PATH } from "@/lib/routes";
import { composerColumn } from "@/lib/ui";

/** The greeting changes at these hours of the day (local time). */
const AFTERNOON_FROM_HOUR = 12;
const EVENING_FROM_HOUR = 18;
/** The screen names a few of the latest made files; the rest are on /library. */
const RECENT_FILES = 4;

function greeting(now: Date): string {
  const h = now.getHours();
  return h < AFTERNOON_FROM_HOUR ? "Good morning" : h < EVENING_FROM_HOUR ? "Good afternoon" : "Good evening";
}

/** A new chat (BACKLOG L24 U6, like Claude's): a greeting with the user's name, the message box in the middle of
 *  the screen, and the latest files the assistant made under it. */
export default function NewChatScreen({
  username,
  threads,
  composer,
}: {
  username: string;
  threads: ChatThread[];
  composer: ReactNode;
}) {
  const router = useRouter();
  const { openChat } = useOpenChat();
  const { files } = useLibrary();
  const recent = (files || []).slice(0, RECENT_FILES);

  return (
    <div className="flex min-h-0 flex-1 flex-col justify-center overflow-y-auto py-8">
      <h1 className="m-0 mb-5 px-4 text-center text-[clamp(1.5rem,3.5vw,2.2rem)] font-normal text-text [font-family:var(--font-display)]">
        {greeting(new Date())}
        {username ? `, ${username}` : ""}
      </h1>
      {composer}
      {recent.length ? (
        <section className={`${composerColumn} mt-4`}>
          <div className="flex items-center justify-between px-1">
            <h2 className="m-0 text-[0.75rem] font-medium text-muted">Recent files</h2>
            <button type="button" className="text-[0.75rem] text-muted hover:text-text" onClick={() => router.push(LIBRARY_PATH)}>
              All files
            </button>
          </div>
          <ul className="m-0 list-none p-0">
            {recent.map((f) => {
              const thread = threads.find((t) => t.id === f.thread_id);
              return (
                <FileRow key={`${f.job_id}/${f.filename}`} file={f} onOpenChat={thread ? () => openChat(thread) : undefined} />
              );
            })}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
