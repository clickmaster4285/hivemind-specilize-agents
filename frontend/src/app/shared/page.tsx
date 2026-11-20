"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useApp } from "@/components/app/AppProvider";
import PageHeader from "@/components/app/PageHeader";
import { useLiveTopic } from "@/hooks/useLiveEvents";
import { listSharedWithMe, type SharedChat } from "@/lib/api";
import { errorText } from "@/lib/chat";
import { sharedChatPath } from "@/lib/routes";
import { table } from "@/lib/ui";

/** /shared (BACKLOG L30 T4): the chats others shared with me — directly or through a project — read-only. */
export default function SharedPage() {
  const app = useApp();
  const [chats, setChats] = useState<SharedChat[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => listSharedWithMe().then(setChats).catch((err: unknown) => setError(errorText(err))), []);
  useLiveTopic(["chats"], load, Boolean(app.auth.user));      // a share given or taken away shows at once

  useEffect(() => {
    let cancelled = false;
    listSharedWithMe()
      .then((list) => !cancelled && setChats(list))
      .catch((err: unknown) => !cancelled && setError(errorText(err)));
    return () => {
      cancelled = true;
    };
  }, []);
  if (!app.auth.user) return null;

  return (
    <PageHeader title="Shared with me" about="Chats your colleagues shared with you — read-only; continue any in a chat of your own.">
      {error ? <p className="mt-6 text-sm text-danger">{error}</p> : null}
      {chats && !chats.length ? (
        <p className="mt-6 text-[0.9rem] text-muted">Nothing is shared with you yet. Ask a colleague to use “Share…” on a chat or a project.</p>
      ) : null}
      {chats?.length ? (
        <div className={`${table.wrap} mt-6`}>
          <table className={table.table}>
            <thead className={table.head}>
              <tr>
                <th className={table.th}>Chat</th>
                <th className={table.th}>From</th>
                <th className={`${table.th} hidden sm:table-cell`}>Project</th>
                <th className={`${table.th} hidden md:table-cell`}>Last active</th>
              </tr>
            </thead>
            <tbody>
              {chats.map((c) => (
                <tr key={c.thread_id} className={table.row}>
                  <td className={table.td}>
                    <Link href={sharedChatPath(c.thread_id)} className="font-semibold hover:underline">{c.title}</Link>
                  </td>
                  <td className={`${table.td} text-muted`}>{c.owner}</td>
                  <td className={`${table.td} hidden sm:table-cell text-muted`}>{c.project ?? "—"}</td>
                  <td className={`${table.td} hidden md:table-cell whitespace-nowrap text-muted`}>
                    {c.updated_at ? new Date(c.updated_at).toLocaleDateString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </PageHeader>
  );
}
