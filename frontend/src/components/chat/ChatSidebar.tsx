"use client";

import { useState } from "react";

import type { HistoryApi } from "@/hooks/useHistory";
import type { ChatThread } from "@/lib/threads";
import type { Health } from "@/lib/types";

type Props = {
  username: string;
  history: HistoryApi;
  health: Health | null;
  onNewChat: () => void;
  onOpenThread: (thread: ChatThread) => void;
  onDeleteThread: (thread: ChatThread) => void;
  onOpenSettings: () => void;
  onSignOut: () => void;
};

// Inline Tailwind building blocks (styling stays with the component; shared classes — brand-mark,
// icon-btn, status/dot, btn — remain global utilities).
const sectionHead = "flex items-center justify-between gap-2";
const sectionTitle = "m-0 text-[0.95rem] [font-family:var(--font-display)]";
const list = "list-none m-0 px-[0.15rem] pb-[0.2rem] flex flex-1 min-h-0 flex-col gap-[0.3rem] overflow-x-hidden overflow-y-auto [overscroll-behavior:contain]";
const navActive = "bg-[rgba(208,138,60,0.1)] border-[color-mix(in_srgb,var(--copper)_35%,var(--line))]";
const navHover = "hover:bg-[rgba(208,138,60,0.1)] hover:border-[color-mix(in_srgb,var(--copper)_35%,var(--line))]";
const navBase = `w-full border border-transparent bg-transparent text-left font-[inherit] text-[var(--text)] rounded-[0.65rem] cursor-pointer ${navHover}`;
const navRow = `${navBase} flex items-center gap-[0.15rem] py-[0.2rem] pl-[0.15rem] pr-[0.25rem]`;
const navMain = "flex-1 min-w-0 border-0 bg-transparent text-left font-[inherit] text-inherit cursor-pointer px-[0.4rem] py-[0.35rem]";
const navMainTitle = "block truncate text-[0.86rem]";
const navMainSub = "block text-[0.72rem] text-[var(--muted)] mt-[0.15rem]";

/** The left rail: new chat, history / projects, settings, sign out. */
export default function ChatSidebar({
  username,
  history,
  health,
  onNewChat,
  onOpenThread,
  onDeleteThread,
  onOpenSettings,
  onSignOut,
}: Props) {
  const [tab, setTab] = useState<"history" | "projects">("history");

  const tabBtn = (on: boolean) =>
    `rounded-[0.55rem] border-0 px-2 py-[0.45rem] font-[inherit] cursor-pointer ${
      on ? "bg-[var(--panel-2)] text-[var(--text)]" : "bg-transparent text-[var(--muted)]"
    }`;

  return (
    <aside
      className="flex h-full min-h-0 flex-col gap-3 overflow-hidden border-r border-[var(--line)] bg-[rgba(14,17,22,0.92)] px-[0.85rem] py-4 max-[900px]:h-auto max-[900px]:max-h-none max-[900px]:border-r-0 max-[900px]:border-b"
      aria-label="Chat navigation"
    >
      <div className="flex shrink-0 items-center gap-[0.7rem] px-[0.35rem] pt-[0.2rem] pb-[0.4rem]">
        <span className="brand-mark sm" aria-hidden />
        <div>
          <strong className="block text-[1.05rem] [font-family:var(--font-display)]">MulteAgent</strong>
          <span className="text-xs text-[var(--muted)]">{username}</span>
        </div>
      </div>

      <button type="button" className="btn primary full shrink-0 mt-[0.15rem]" onClick={onNewChat}>
        New chat
      </button>

      <div className="grid shrink-0 grid-cols-2 gap-[0.35rem] rounded-[0.7rem] border border-[var(--line)] bg-white/[0.02] p-[0.2rem]">
        <button type="button" className={tabBtn(tab === "history")} onClick={() => setTab("history")}>
          History
        </button>
        <button type="button" className={tabBtn(tab === "projects")} onClick={() => setTab("projects")}>
          Projects
        </button>
      </div>

      {tab === "projects" ? (
        <ProjectList
          history={history}
          onNew={() => {
            if (history.newProject()) setTab("projects");
          }}
          onSelect={(id) => {
            history.selectProject(id);
            setTab("history");
          }}
        />
      ) : (
        <ThreadList history={history} onOpen={onOpenThread} onDelete={onDeleteThread} />
      )}

      <div className="mt-auto flex shrink-0 gap-[0.45rem] border-t border-[var(--line)] pt-[0.6rem]">
        <button type="button" className="btn ghost full" onClick={onOpenSettings}>
          Settings
        </button>
        <button type="button" className="btn ghost full" onClick={onSignOut}>
          Sign out
        </button>
      </div>
    </aside>
  );
}

function ProjectList({
  history,
  onNew,
  onSelect,
}: {
  history: HistoryApi;
  onNew: () => void;
  onSelect: (id: string | null) => void;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-[0.45rem] overflow-hidden">
      <div className={sectionHead}>
        <h2 className={sectionTitle}>Projects</h2>
        <button type="button" className="btn ghost sm" onClick={onNew}>
          New
        </button>
      </div>
      <ul className={list}>
        <li>
          <button
            type="button"
            className={`${navBase} px-[0.65rem] py-[0.55rem] ${history.projectId ? "" : navActive}`}
            onClick={() => onSelect(null)}
          >
            All chats
          </button>
        </li>
        {history.projects.map((p) => (
          <li key={p.id}>
            <div className={`${navRow} ${history.projectId === p.id ? navActive : ""}`}>
              <button type="button" className={navMain} onClick={() => onSelect(p.id)}>
                <strong className={navMainTitle}>{p.name}</strong>
                <span className={navMainSub}>{p.jobIds.length} chats</span>
              </button>
              <button
                type="button"
                className="icon-btn"
                title="Delete project"
                onClick={() => {
                  if (confirm(`Delete project “${p.name}”?`)) history.removeProject(p.id);
                }}
              >
                ×
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ThreadList({
  history,
  onOpen,
  onDelete,
}: {
  history: HistoryApi;
  onOpen: (thread: ChatThread) => void;
  onDelete: (thread: ChatThread) => void;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-[0.45rem] overflow-hidden">
      <div className={sectionHead}>
        <h2 className={sectionTitle}>{history.activeProject ? history.activeProject.name : "History"}</h2>
        <button type="button" className="btn ghost sm" onClick={() => void history.refreshJobs()}>
          Refresh
        </button>
      </div>
      <input
        className="w-full rounded-[0.55rem] border border-[var(--line)] bg-[var(--panel-2)] px-[0.65rem] py-2 font-[inherit] text-[var(--text)] outline-none"
        placeholder="Search chats…"
        value={history.filter}
        onChange={(e) => history.setFilter(e.target.value)}
      />
      <ul className={list}>
        {history.filteredThreads.length === 0 ? (
          <li className="px-[0.4rem] py-[0.6rem] text-[0.8rem] text-[var(--muted)]">No chats yet. Start a new one.</li>
        ) : (
          history.filteredThreads.map((thread) => {
            const lastJob = [...thread.jobIds]
              .reverse()
              .map((id) => history.jobById.get(id))
              .find(Boolean);
            const turns = thread.jobIds.length;
            const active = history.activeThreadId === thread.id;
            const pinned = thread.pinned
              ? "border-[color-mix(in_srgb,var(--copper)_40%,var(--line))] bg-[rgba(208,138,60,0.06)]"
              : "";
            return (
              <li key={thread.id}>
                <div className={`${navRow} ${active ? navActive : ""} ${pinned}`}>
                  <button type="button" className={navMain} onClick={() => onOpen(thread)}>
                    <strong className={navMainTitle}>{thread.title}</strong>
                    <span className={navMainSub}>
                      {turns} turn{turns === 1 ? "" : "s"}
                      {lastJob ? ` · ${lastJob.status}` : ""}
                    </span>
                  </button>
                  <button
                    type="button"
                    className={`icon-btn ${thread.pinned ? "on" : ""}`}
                    title={thread.pinned ? "Unpin chat" : "Pin chat"}
                    onClick={(e) => {
                      e.stopPropagation();
                      history.togglePin(thread);
                    }}
                  >
                    {thread.pinned ? "★" : "☆"}
                  </button>
                  <button
                    type="button"
                    className="icon-btn danger"
                    title="Delete chat"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete(thread);
                    }}
                  >
                    ×
                  </button>
                </div>
              </li>
            );
          })
        )}
      </ul>
    </div>
  );
}
