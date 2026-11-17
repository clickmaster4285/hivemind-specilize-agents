"use client";

import { useEffect, useRef, useState } from "react";

import {
  IconFolder, IconLogout, IconNewChat, IconPanel, IconPlug, IconSearch, IconSettings,
} from "@/components/app/NavIcons";
import type { HistoryApi } from "@/hooks/useHistory";
import { groupThreads, threadDate } from "@/lib/threadGroups";
import type { ChatThread } from "@/lib/threads";
import { brandMark, btn, iconBtn } from "@/lib/ui";

type Props = {
  username: string;
  role: string;
  history: HistoryApi;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  pluginsOpen: boolean;
  onNewChat: () => void;
  onOpenThread: (thread: ChatThread) => void;
  onDeleteThread: (thread: ChatThread) => void;
  onOpenPlugins: () => void;
  onOpenSettings: () => void;
  onSignOut: () => void;
};

// Inline Tailwind building blocks (styling stays with the component; shared classes — brand-mark,
// icon-btn, btn — remain global utilities).
const sectionHead = "flex items-center justify-between gap-2";
const sectionTitle = "m-0 text-[0.95rem] [font-family:var(--font-display)]";
const list = "list-none m-0 px-[0.15rem] pb-[0.2rem] flex flex-1 min-h-0 flex-col gap-[0.1rem] overflow-x-hidden overflow-y-auto [overscroll-behavior:contain]";
// `!` forces these over navBase's `bg-transparent border-transparent` (Tailwind resolves by CSS order,
// not class order, so without `!` the active highlight was being overridden and never showed).
const navActive = "!bg-[color-mix(in_srgb,var(--copper)_20%,var(--panel-2))] !border-copper";
const navHover = "hover:bg-[var(--copper-soft)] hover:border-[color-mix(in_srgb,var(--copper)_35%,var(--line))]";
const navBase = `w-full border border-transparent bg-transparent text-left font-[inherit] text-text rounded-[0.65rem] cursor-pointer ${navHover}`;
const navRow = `${navBase} flex items-center gap-[0.15rem] py-[0.05rem] pl-[0.15rem] pr-1`;
const navMain = "flex-1 min-w-0 border-0 bg-transparent text-left font-[inherit] text-inherit cursor-pointer px-[0.4rem] py-[0.35rem]";
const navMainTitle = "block truncate text-[0.86rem] font-normal";
const navMainSub = "block text-[0.72rem] text-muted mt-[0.15rem]";
const menuRow = "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[0.8rem] text-text hover:bg-hover";
const groupLabel = "px-[0.55rem] pb-[0.15rem] pt-[0.6rem] text-[0.7rem] font-medium text-muted";
// a nav entry (New chat, Search, Projects, Plugins): icon + label, or the icon alone on the collapsed rail
const navItem = (on: boolean, collapsed: boolean) =>
  `${navBase} flex items-center gap-[0.6rem] py-[0.45rem] text-[0.86rem] ${collapsed ? "justify-center px-0" : "px-[0.6rem]"} ${on ? navActive : ""}`;

/** The hover card waits this long, so moving the pointer down the list doesn't flash a card on every row. */
const HOVER_CARD_DELAY_MS = 450;

function Dots() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" aria-hidden>
      <circle cx="12" cy="5" r="1.7" fill="currentColor" />
      <circle cx="12" cy="12" r="1.7" fill="currentColor" />
      <circle cx="12" cy="19" r="1.7" fill="currentColor" />
    </svg>
  );
}

/**
 * The left rail (BACKLOG L24 U2–U4): brand and collapse toggle; New chat, Search, Projects, Plugins; the chats
 * grouped by when they were used, one line each with a hover card (full title, date); the account menu at the
 * bottom (Settings, Log out). Collapsed, it is a thin rail of icons.
 */
export default function ChatSidebar({
  username,
  role,
  history,
  collapsed,
  onToggleCollapsed,
  pluginsOpen,
  onNewChat,
  onOpenThread,
  onDeleteThread,
  onOpenPlugins,
  onOpenSettings,
  onSignOut,
}: Props) {
  const [tab, setTab] = useState<"history" | "projects">("history");
  const [searching, setSearching] = useState(false);
  const [focusSearch, setFocusSearch] = useState(0);
  const expandThen = (fn: () => void) => () => {
    if (collapsed) onToggleCollapsed();
    fn();
  };

  return (
    <aside
      className={`flex h-full min-h-0 flex-col gap-2 overflow-hidden border-r border-line bg-glass py-3 max-[900px]:h-auto max-[900px]:max-h-none max-[900px]:border-r-0 max-[900px]:border-b ${
        collapsed ? "items-stretch px-[0.45rem]" : "px-[0.7rem]"
      }`}
      aria-label="Chat navigation"
    >
      <div className={`flex shrink-0 items-center gap-[0.6rem] px-[0.2rem] pb-[0.3rem] ${collapsed ? "flex-col" : ""}`}>
        {collapsed ? null : (
          <>
            <span className={brandMark("sm")} aria-hidden />
            <strong className="min-w-0 flex-1 truncate text-[1.05rem] [font-family:var(--font-display)]">MulteAgent</strong>
          </>
        )}
        <button
          type="button"
          className={iconBtn()}
          onClick={onToggleCollapsed}
          title={collapsed ? "Open sidebar" : "Close sidebar"}
          aria-label={collapsed ? "Open sidebar" : "Close sidebar"}
          aria-expanded={!collapsed}
        >
          <IconPanel />
        </button>
      </div>

      <nav className="flex shrink-0 flex-col gap-[0.1rem]" aria-label="Main">
        <button type="button" className={navItem(false, collapsed)} onClick={onNewChat} title="New chat">
          <IconNewChat />
          {collapsed ? null : <span>New chat</span>}
        </button>
        <button
          type="button"
          className={navItem(searching, collapsed)}
          title="Search chats"
          onClick={expandThen(() => {
            setTab("history");
            setSearching(true);
            setFocusSearch((n) => n + 1);
          })}
        >
          <IconSearch />
          {collapsed ? null : <span>Search</span>}
        </button>
        <button
          type="button"
          className={navItem(tab === "projects" && !collapsed, collapsed)}
          title="Projects"
          onClick={expandThen(() => setTab(tab === "projects" && !collapsed ? "history" : "projects"))}
        >
          <IconFolder />
          {collapsed ? null : <span>Projects</span>}
        </button>
        <button type="button" className={navItem(pluginsOpen, collapsed)} title="Plugins" onClick={onOpenPlugins}>
          <IconPlug />
          {collapsed ? null : <span>Plugins</span>}
        </button>
      </nav>

      {collapsed ? (
        <div className="flex-1" />
      ) : tab === "projects" ? (
        <ProjectList
          history={history}
          onNew={() => {
            void history.newProject().then((p) => {
              if (p) setTab("projects");
            });
          }}
          onSelect={(id) => {
            history.selectProject(id);
            setTab("history");
          }}
        />
      ) : (
        <ThreadList
          history={history}
          searching={searching}
          focusSearch={focusSearch}
          onCloseSearch={() => {
            setSearching(false);
            history.setFilter("");
          }}
          onOpen={onOpenThread}
          onDelete={onDeleteThread}
        />
      )}

      <AccountMenu
        username={username}
        role={role}
        collapsed={collapsed}
        onOpenSettings={onOpenSettings}
        onSignOut={onSignOut}
      />
    </aside>
  );
}

/** The account button at the bottom (name, role) and its menu, like ChatGPT / Claude / DeepSeek (L24 U4). */
function AccountMenu({
  username,
  role,
  collapsed,
  onOpenSettings,
  onSignOut,
}: {
  username: string;
  role: string;
  collapsed: boolean;
  onOpenSettings: () => void;
  onSignOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  const initial = (username[0] || "?").toUpperCase();
  return (
    <div className="relative mt-auto shrink-0 border-t border-line pt-2">
      {open ? (
        <>
          <button type="button" className="fixed inset-0 z-40 cursor-default" aria-hidden tabIndex={-1} onClick={() => setOpen(false)} />
          <div
            role="menu"
            className={`absolute bottom-full z-50 mb-2 rounded-xl border border-line bg-panel p-1.5 shadow-shadow ${
              collapsed ? "left-0 w-52" : "left-0 right-0"
            }`}
          >
            <p className="truncate px-2.5 pb-1 pt-1 text-[0.72rem] text-muted">{username}</p>
            <button type="button" role="menuitem" className={menuRow} onClick={() => { setOpen(false); onOpenSettings(); }}>
              <IconSettings />
              Settings
            </button>
            <div className="my-1 border-t border-line" />
            <button type="button" role="menuitem" className={menuRow} onClick={() => { setOpen(false); onSignOut(); }}>
              <IconLogout />
              Log out
            </button>
          </div>
        </>
      ) : null}
      <button
        type="button"
        className={`${navBase} flex items-center gap-[0.6rem] py-[0.4rem] ${collapsed ? "justify-center px-0" : "px-[0.45rem]"}`}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={collapsed ? username : undefined}
      >
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-copper text-[0.8rem] font-semibold text-ink">
          {initial}
        </span>
        {collapsed ? null : (
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[0.86rem]">{username}</span>
            <span className="block truncate text-[0.7rem] capitalize text-muted">{role}</span>
          </span>
        )}
      </button>
    </div>
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
        <button type="button" className={btn("ghost", "sm")} onClick={onNew}>
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
                <span className={navMainSub}>{history.threads.filter((t) => t.projectId === p.id).length} chats</span>
              </button>
              <button
                type="button"
                className={iconBtn()}
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
  searching,
  focusSearch,
  onCloseSearch,
  onOpen,
  onDelete,
}: {
  history: HistoryApi;
  searching: boolean;
  focusSearch: number;
  onCloseSearch: () => void;
  onOpen: (thread: ChatThread) => void;
  onDelete: (thread: ChatThread) => void;
}) {
  // the per-chat ⋮ menu — one at a time, positioned from the button so the scrolling list can't clip it
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const menuThread = history.filteredThreads.find((t) => t.id === menu?.id) || null;
  // the hover card: full title and date beside the row (one-line titles are cut with "…", L24 U3)
  const [card, setCard] = useState<{ thread: ChatThread; x: number; y: number } | null>(null);
  const hoverTimer = useRef<number | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (searching) searchRef.current?.focus();
  }, [searching, focusSearch]);

  function hoverStart(thread: ChatThread, el: HTMLElement) {
    if (hoverTimer.current !== null) window.clearTimeout(hoverTimer.current);
    const r = el.getBoundingClientRect();
    hoverTimer.current = window.setTimeout(() => setCard({ thread, x: r.right + 8, y: r.top }), HOVER_CARD_DELAY_MS);
  }
  function hoverEnd() {
    if (hoverTimer.current !== null) window.clearTimeout(hoverTimer.current);
    hoverTimer.current = null;
    setCard(null);
  }

  const groups = groupThreads(history.filteredThreads);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-[0.35rem] overflow-hidden">
      {history.activeProject ? (
        <div className={sectionHead}>
          <h2 className={sectionTitle}>{history.activeProject.name}</h2>
          <button type="button" className={btn("ghost", "sm")} onClick={() => history.selectProject(null)}>
            All chats
          </button>
        </div>
      ) : null}
      {searching ? (
        <div className="flex items-center gap-1">
          <input
            ref={searchRef}
            className="w-full rounded-[0.55rem] border border-line bg-panel-2 px-[0.65rem] py-[0.45rem] font-[inherit] text-[0.85rem] text-text outline-none"
            placeholder="Search chats…"
            value={history.filter}
            onChange={(e) => history.setFilter(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") onCloseSearch();
            }}
          />
          <button type="button" className={iconBtn()} title="Close search" aria-label="Close search" onClick={onCloseSearch}>
            ×
          </button>
        </div>
      ) : null}
      <ul className={list} onScroll={hoverEnd}>
        {groups.length === 0 ? (
          <li className="px-[0.4rem] py-[0.6rem] text-[0.8rem] text-muted">
            {history.filter ? "No chats match." : "No chats yet. Start a new one."}
          </li>
        ) : (
          groups.map((group) => (
            <li key={group.label}>
              <h3 className={groupLabel}>{group.label}</h3>
              <ul className="m-0 flex list-none flex-col gap-[0.1rem] p-0">
                {group.threads.map((thread) => {
                  const active = history.activeThreadId === thread.id;
                  return (
                    <li key={thread.id}>
                      <div
                        className={`${navRow} group ${active ? navActive : ""}`}
                        onMouseEnter={(e) => hoverStart(thread, e.currentTarget)}
                        onMouseLeave={hoverEnd}
                      >
                        <button type="button" className={navMain} onClick={() => { hoverEnd(); onOpen(thread); }}>
                          <span className={navMainTitle}>{thread.title}</span>
                        </button>
                        <button
                          type="button"
                          className={`${menu?.id === thread.id ? iconBtn("on") : iconBtn()} opacity-0 group-hover:opacity-100 focus:opacity-100 ${
                            menu?.id === thread.id || active ? "opacity-100" : ""
                          }`}
                          title="More"
                          aria-label="More"
                          onClick={(e) => {
                            e.stopPropagation();
                            hoverEnd();
                            const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                            setMenu(menu?.id === thread.id ? null : { id: thread.id, x: r.right, y: r.bottom });
                          }}
                        >
                          <Dots />
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </li>
          ))
        )}
      </ul>
      {card ? (
        <div
          role="tooltip"
          className="pointer-events-none fixed z-50 max-w-72 rounded-xl border border-line bg-panel px-3 py-2 shadow-shadow"
          style={{ top: card.y, left: card.x }}
        >
          <p className="m-0 text-[0.85rem] leading-snug text-text">{card.thread.title}</p>
          <p className="m-0 mt-1 text-[0.72rem] text-muted">
            {threadDate(card.thread)} · {card.thread.jobIds.length} message{card.thread.jobIds.length === 1 ? "" : "s"}
          </p>
        </div>
      ) : null}
      {menu && menuThread ? (
        <>
          <button
            type="button"
            className="fixed inset-0 z-40 cursor-default"
            aria-hidden
            tabIndex={-1}
            onClick={() => setMenu(null)}
          />
          <div
            className="fixed z-50 w-48 rounded-lg border border-line bg-panel p-1 shadow-shadow"
            style={{ top: menu.y + 4, left: Math.max(8, menu.x - 192) }}
          >
            <button type="button" className={menuRow} onClick={() => { setMenu(null); history.togglePin(menuThread); }}>
              <span>📌</span>
              {menuThread.pinned ? "Unpin" : "Pin"}
            </button>
            <button
              type="button"
              className={menuRow}
              onClick={() => {
                setMenu(null);
                const title = window.prompt("Rename chat", menuThread.title);
                if (title && title.trim()) history.rename(menuThread, title.trim());
              }}
            >
              <span>✏️</span>
              Rename
            </button>
            {history.projects.length ? (
              <>
                <div className="my-1 border-t border-line" />
                <p className="px-2.5 pb-0.5 pt-1 text-[0.64rem] uppercase tracking-wide text-muted">Move to project</p>
                {history.projects.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    className={menuRow}
                    onClick={() => { setMenu(null); history.moveToProject(menuThread, p.id); }}
                  >
                    <span>📁</span>
                    {p.name}
                  </button>
                ))}
                {menuThread.projectId ? (
                  <button
                    type="button"
                    className={menuRow}
                    onClick={() => { setMenu(null); history.moveToProject(menuThread, null); }}
                  >
                    <span>↩️</span>
                    Remove from project
                  </button>
                ) : null}
              </>
            ) : null}
            <div className="my-1 border-t border-line" />
            <button
              type="button"
              className={`${menuRow} text-danger-text`}
              onClick={() => { setMenu(null); onDelete(menuThread); }}
            >
              <span>🗑️</span>
              Delete
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}
