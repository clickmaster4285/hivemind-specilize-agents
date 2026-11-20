"use client";

import { useCallback, useEffect, useState } from "react";

import { useApp } from "@/components/app/AppProvider";
import { listPeople, listTeamDocs, removeTeamDoc, uploadTeamDoc, type TeamDoc } from "@/lib/api";
import { errorText, fileToBase64 } from "@/lib/chat";
import { btn, formFieldCompact, table } from "@/lib/ui";

const ACCEPT = ".pdf,.docx,.md,.markdown,.txt,.html,.htm";

/** Settings → Team knowledge (BACKLOG L30 T4): documents a team's searches find, and only that team's — runbooks,
 *  policies. Members see their team's list; admins choose a team, add files (PDF, Word, Markdown, text, HTML) and
 *  remove them. The same file name again replaces the document. */
export default function TeamKnowledgeSection() {
  const user = useApp().auth.user;
  const isAdmin = user?.role === "admin";
  const [teams, setTeams] = useState<string[]>([]);
  const [team, setTeam] = useState(user?.team ?? "");
  const [docs, setDocs] = useState<TeamDoc[] | null>(null);
  const [note, setNote] = useState<{ text: string; bad: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    listPeople()
      .then((people) => {
        if (cancelled) return;
        const found = [...new Set([user?.team, ...people.map((p) => p.team)].filter(Boolean))].sort() as string[];
        setTeams(found);
        setTeam((t) => t || found[0] || "");
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [isAdmin, user?.team]);

  const load = useCallback(
    (forTeam: string) =>
      listTeamDocs(isAdmin ? forTeam : undefined)
        .then((r) => setDocs(r.documents))
        .catch((err: unknown) => setNote({ text: errorText(err), bad: true })),
    [isAdmin],
  );
  useEffect(() => {
    if (!team && isAdmin) return;
    let cancelled = false;
    listTeamDocs(isAdmin ? team : undefined)
      .then((r) => !cancelled && setDocs(r.documents))
      .catch((err: unknown) => !cancelled && setNote({ text: errorText(err), bad: true }));
    return () => {
      cancelled = true;
    };
  }, [team, isAdmin]);

  async function upload(files: FileList | null) {
    if (!files?.length || !team) return;
    setNote(null);
    for (const file of Array.from(files)) {
      setBusy(`Reading ${file.name}…`);
      try {
        const done = await uploadTeamDoc(team, file.name, await fileToBase64(file));
        setNote({ text: `${file.name}: ${done.replaced ? "replaced" : "added"} (${done.chunks} passages).`, bad: false });
      } catch (err) {
        setNote({ text: `${file.name}: ${errorText(err)}`, bad: true });
      }
    }
    setBusy(null);
    await load(team);
  }

  async function remove(doc: TeamDoc) {
    if (!confirm(`Remove “${doc.title}” from ${team}'s knowledge?`)) return;
    try {
      await removeTeamDoc(team, doc.id);
      await load(team);
    } catch (err) {
      setNote({ text: errorText(err), bad: true });
    }
  }

  if (!user) return null;
  return (
    <div className="flex flex-col gap-3">
      <div>
        <h4 className="m-0 text-base font-semibold [font-family:var(--font-display)]">Team knowledge</h4>
        <p className="m-0 mt-1 text-xs text-muted">
          Documents the assistant uses when someone of that team asks — and only them. {isAdmin
            ? "Add runbooks, policies or manuals: PDF, Word, Markdown, text or HTML (up to 20 MB); the same name again replaces it."
            : user.team ? `Your team: ${user.team}. An admin adds the documents.` : "You are in no team yet — ask an admin."}
        </p>
      </div>
      {isAdmin ? (
        <div className="flex flex-wrap items-center gap-2">
          {teams.length ? (
            <select className={formFieldCompact} value={team} onChange={(e) => setTeam(e.target.value)} aria-label="Team">
              {teams.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          ) : (
            <span className="text-xs text-muted">Give people a team in Settings → Users first.</span>
          )}
          <label className={`${btn("primary", "sm")} ${!team || busy ? "pointer-events-none opacity-50" : ""}`}>
            Add files
            <input type="file" multiple accept={ACCEPT} className="sr-only" disabled={!team || Boolean(busy)}
                   onChange={(e) => {
                     void upload(e.target.files);
                     e.target.value = "";
                   }} />
          </label>
          {busy ? <span className="text-xs text-muted">{busy}</span> : null}
        </div>
      ) : null}
      {note ? <p role="status" className={`m-0 text-xs ${note.bad ? "text-danger" : "text-muted"}`}>{note.text}</p> : null}
      {docs && !docs.length ? <p className="m-0 text-xs text-muted">No documents for this team yet.</p> : null}
      {docs?.length ? (
        <div className={table.wrap}>
          <table className={table.table}>
            <thead className={table.head}>
              <tr>
                <th className={table.th}>Document</th>
                <th className={`${table.th} hidden md:table-cell`}>Added</th>
                <th className={`${table.th} hidden sm:table-cell text-right`}>Passages</th>
                {isAdmin ? <th className={table.th}><span className="sr-only">Actions</span></th> : null}
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id} className={table.row}>
                  <td className={table.td}>
                    <span className="font-semibold">{d.title}</span>
                    <small className="block text-xs text-muted">{d.file}</small>
                  </td>
                  <td className={`${table.td} hidden md:table-cell whitespace-nowrap text-muted`}>
                    {new Date(d.uploaded_at).toLocaleDateString()}{d.uploaded_by ? ` · ${d.uploaded_by}` : ""}
                  </td>
                  <td className={`${table.td} hidden sm:table-cell text-right tabular-nums`}>{d.chunks}</td>
                  {isAdmin ? (
                    <td className={`${table.td} text-right`}>
                      <button type="button" className={btn("ghost", "sm")} onClick={() => void remove(d)}>Remove</button>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
