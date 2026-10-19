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

  return (
    <aside className="chat-rail" aria-label="Chat navigation">
      <div className="rail-brand">
        <span className="brand-mark sm" aria-hidden />
        <div>
          <strong>MulteAgent</strong>
          <span>{username}</span>
        </div>
      </div>

      <button type="button" className="btn primary full new-chat-btn" onClick={onNewChat}>
        New chat
      </button>

      <div className="rail-tabs">
        <button type="button" className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>
          History
        </button>
        <button type="button" className={tab === "projects" ? "active" : ""} onClick={() => setTab("projects")}>
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

      <div className="rail-footer">
        <button type="button" className="btn ghost full" onClick={onOpenSettings}>
          Settings · specialists & models
        </button>
        <div className={`status ${health?.llm_reachable ? "ok" : "bad"}`}>
          <span className="dot" />
          <span>
            {health
              ? `q ${health.queue_depth ?? 0} · ~${Number(health.estimated_wait_seconds || 0).toFixed(0)}s`
              : "API down"}
          </span>
        </div>
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
    <div className="rail-section grow">
      <div className="rail-section-head">
        <h2>Projects</h2>
        <button type="button" className="btn ghost sm" onClick={onNew}>
          New
        </button>
      </div>
      <ul className="history-list">
        <li>
          <button
            type="button"
            className={`nav-item ${history.projectId ? "" : "active"}`}
            onClick={() => onSelect(null)}
          >
            All chats
          </button>
        </li>
        {history.projects.map((p) => (
          <li key={p.id}>
            <div className={`nav-item row ${history.projectId === p.id ? "active" : ""}`}>
              <button type="button" className="nav-main" onClick={() => onSelect(p.id)}>
                <strong>{p.name}</strong>
                <span>{p.jobIds.length} chats</span>
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
    <div className="rail-section grow">
      <div className="rail-section-head">
        <h2>{history.activeProject ? history.activeProject.name : "History"}</h2>
        <button type="button" className="btn ghost sm" onClick={() => void history.refreshJobs()}>
          Refresh
        </button>
      </div>
      <input
        className="search-input"
        placeholder="Search chats…"
        value={history.filter}
        onChange={(e) => history.setFilter(e.target.value)}
      />
      <ul className="history-list">
        {history.filteredThreads.length === 0 ? (
          <li className="empty-side">No chats yet. Start a new one.</li>
        ) : (
          history.filteredThreads.map((thread) => {
            const lastJob = [...thread.jobIds]
              .reverse()
              .map((id) => history.jobById.get(id))
              .find(Boolean);
            const turns = thread.jobIds.length;
            return (
              <li key={thread.id}>
                <div
                  className={`nav-item row ${history.activeThreadId === thread.id ? "active" : ""} ${
                    thread.pinned ? "pinned" : ""
                  }`}
                >
                  <button type="button" className="nav-main" onClick={() => onOpen(thread)}>
                    <strong>{thread.title}</strong>
                    <span>
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
