"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useApp } from "@/components/app/AppProvider";
import { openShareLink } from "@/lib/api";
import { errorText } from "@/lib/chat";
import { chatPath, sharedChatPath, SHARED_PATH } from "@/lib/routes";
import { btn } from "@/lib/ui";

/** /s/<token> — a share link (BACKLOG L30 T4). Signed out, the shell shows the sign-in first and this page runs after
 *  it. Opening the link makes the person a reader; then the chat opens: read-only, or as their own chat if it is. */
export default function ShareLinkPage() {
  const app = useApp();
  const router = useRouter();
  const { token } = useParams<{ token: string }>();
  const signedIn = Boolean(app.auth.user) && !app.auth.user?.must_change_password;
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    openShareLink(decodeURIComponent(token))
      .then((found) => !cancelled && router.replace(found.own ? chatPath(found.thread_id) : sharedChatPath(found.thread_id)))
      .catch((err: unknown) => !cancelled && setProblem(errorText(err)));
    return () => {
      cancelled = true;
    };
  }, [signedIn, token, router]);

  return (
    <div className="mx-auto mt-16 flex max-w-md flex-col items-center gap-3 px-4 text-center">
      {problem ? (
        <>
          <p role="alert" className="m-0 text-sm text-danger">{problem}</p>
          <button type="button" className={btn("ghost", "sm")} onClick={() => router.push(SHARED_PATH)}>
            Shared with me
          </button>
        </>
      ) : (
        <p className="m-0 text-sm text-muted">Opening the shared chat…</p>
      )}
    </div>
  );
}
