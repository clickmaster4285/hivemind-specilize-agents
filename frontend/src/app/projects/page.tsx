"use client";

import { useState } from "react";

import { useApp } from "@/components/app/AppProvider";
import PageHeader, { pageSearch } from "@/components/app/PageHeader";
import ShareDialog from "@/components/app/ShareDialog";
import { useOpenChat } from "@/hooks/useOpenChat";
import { threadDate } from "@/lib/threadGroups";
import type { ChatThread } from "@/lib/threads";
import { btn, iconBtn } from "@/lib/ui";

/** Each card names a few of the project's latest chats, like Claude's project list; the rest open from the
 *  sidebar once the project is selected. */
const CHATS_PER_CARD = 3;

const card = "flex min-w-0 flex-col gap-2 rounded-xl border border-line bg-panel p-4";

/** /projects (BACKLOG L24 U5): the user's projects as cards — search, New project, rename, delete. A card's
 *  "New chat" selects the project (new chats join it, the sidebar lists only its chats) and starts a chat. */
export default function ProjectsPage() {
  const app = useApp();
  const { openChat, newChat } = useOpenChat();
  const [search, setSearch] = useState("");
  const [sharing, setSharing] = useState<{ id: string; name: string } | null>(null);
  const history = app.history;
  if (!app.auth.user) return null; // the shell shows the login when signed out

  const needle = search.trim().toLowerCase();
  const projects = history.projects.filter((p) => !needle || p.name.toLowerCase().includes(needle));
  const chatsOf = (id: string): ChatThread[] =>
    history.threads
      .filter((t) => t.projectId === id)
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));

  function startIn(id: string) {
    history.selectProject(id);
    newChat();
  }

  return (
    <PageHeader
      title="Projects"
      about="Group related chats. New chats started in a project stay in it."
      action={
        <button type="button" className={btn("primary", "sm")} onClick={() => void history.newProject()}>
          New project
        </button>
      }
      toolbar={
        <input className={pageSearch} placeholder="Search projects" value={search}
               onChange={(e) => setSearch(e.target.value)} />
      }
    >
      {projects.length === 0 ? (
        <p className="mt-8 text-[0.9rem] text-muted">
          {history.projects.length ? "No project matches." : "No projects yet. Make one to group related chats."}
        </p>
      ) : (
        <ul className="m-0 mt-6 grid list-none grid-cols-2 gap-4 p-0 max-[43.75rem]:grid-cols-1">
          {projects.map((p) => {
            const chats = chatsOf(p.id);
            const active = history.projectId === p.id;
            return (
              <li key={p.id} className={`${card} ${active ? "border-copper" : ""}`}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h2 className="m-0 truncate text-[1rem] font-semibold">{p.name}</h2>
                    <p className="m-0 mt-0.5 text-[0.75rem] text-muted">
                      {chats.length} chat{chats.length === 1 ? "" : "s"}
                      {chats[0] ? ` · last ${threadDate(chats[0])}` : ""}
                    </p>
                  </div>
                  <span className="flex shrink-0 gap-0.5">
                    <button type="button" className={iconBtn()} title="Share project (read-only)" aria-label="Share project"
                            onClick={() => setSharing({ id: p.id, name: p.name })}>
                      👥
                    </button>
                    <button type="button" className={iconBtn()} title="Rename project" aria-label="Rename project"
                            onClick={() => {
                              const name = window.prompt("Rename project", p.name);
                              if (name?.trim()) void history.renameProject(p.id, name.trim());
                            }}>
                      ✏️
                    </button>
                    <button type="button" className={iconBtn()} title="Delete project" aria-label="Delete project"
                            onClick={() => {
                              if (confirm(`Delete project “${p.name}”? Its chats stay, outside any project.`)) {
                                void history.removeProject(p.id);
                              }
                            }}>
                      🗑️
                    </button>
                  </span>
                </div>
                {chats.length ? (
                  <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
                    {chats.slice(0, CHATS_PER_CARD).map((t) => (
                      <li key={t.id}>
                        <button type="button" title={t.title}
                                className="w-full truncate rounded-md px-2 py-1 text-left text-[0.84rem] text-text hover:bg-hover"
                                onClick={() => openChat(t)}>
                          {t.title}
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : null}
                <div className="mt-auto flex gap-2 pt-1">
                  <button type="button" className={btn("ghost", "sm")} onClick={() => startIn(p.id)}>
                    New chat here
                  </button>
                  {chats.length > CHATS_PER_CARD ? (
                    <button type="button" className={btn("ghost", "sm")} onClick={() => history.selectProject(p.id)}>
                      All {chats.length} in the sidebar
                    </button>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {sharing ? (
        <ShareDialog kind="project" id={sharing.id} title={sharing.name} onClose={() => setSharing(null)} />
      ) : null}
    </PageHeader>
  );
}
