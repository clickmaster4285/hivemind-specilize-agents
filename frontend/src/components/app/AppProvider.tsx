"use client";

/**
 * Holds the whole app's state once, above the router, so the sidebar and navbar stay mounted and only
 * the page content (children) changes when you move between / and /settings — and the open chat
 * survives that move. Everything the old ControlDeck did lives here; pages read it with useApp().
 */
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

import { useAuth } from "@/hooks/useAuth";
import { useChat, type SendOptions } from "@/hooks/useChat";
import { useHealth } from "@/hooks/useHealth";
import { useHistory } from "@/hooks/useHistory";
import { useModels } from "@/hooks/useModels";
import { usePendingImages } from "@/hooks/usePendingImages";
import { useSpecialists } from "@/hooks/useSpecialists";
import { useWindowEvent } from "@/hooks/useWindowEvent";
import { confirmCommand, forgetServers, type ImageProfile } from "@/lib/api";
import { chatImagesToPending, errorText, IMAGE_QUESTION, type EditStrength } from "@/lib/chat";
import { markConfirmAnswered } from "@/lib/feedReducer";
import type { ChatThread } from "@/lib/threads";
import type { FeedItem, RunMode } from "@/lib/types";

function useAppState() {
  const health = useHealth();
  const models = useModels();
  const specialists = useSpecialists();
  const history = useHistory();
  const images = usePendingImages();

  const [query, setQuery] = useState("");
  const [runMode, setRunMode] = useState<RunMode>("auto");
  const [allowOverflow] = useState(true);
  const [imageProfile, setImageProfile] = useState<ImageProfile>("fast");
  const [editStrength, setEditStrength] = useState<EditStrength>("medium");
  const [deep, setDeep] = useState(false);          // Deep research toggle (next to Send)
  const queryRef = useRef<HTMLTextAreaElement | null>(null);

  const chat = useChat(
    history,
    {
      mode: runMode,
      specialist: specialists.choice,
      specialistLabel: specialists.label,
      model: models.override,
      allowOverflow,
      imageProfile,
      editStrength,
      deep,
    },
    () => {
      setQuery("");
      images.clear();
    },
  );

  const { load: loadSpecialists, clear: clearSpecialists } = specialists;
  const { refresh: refreshModels } = models;
  const { refreshJobs, loadHistory, clear: clearJobs, selectThread } = history;
  const { setFeed } = chat;
  const onSignedIn = useCallback(
    async (fresh: boolean) => {
      await loadSpecialists();
      await refreshModels(fresh);
      // load the chat/project metadata first so refreshed jobs group under their saved titles/pins
      await loadHistory();
      await refreshJobs();
    },
    [loadSpecialists, refreshModels, loadHistory, refreshJobs],
  );
  const onSignedOut = useCallback(
    (reason: "logout" | "expired") => {
      if (reason === "logout") {
        void forgetServers().catch(() => undefined);
        selectThread(null);
      }
      clearSpecialists();
      clearJobs();
      setFeed([]);
    },
    [clearSpecialists, clearJobs, selectThread, setFeed],
  );
  const auth = useAuth({ onSignedIn, onSignedOut });

  // A diagram that failed to draw asks the assistant to fix it (MermaidDiagram sends "multeagent:ask").
  useWindowEvent<string>("multeagent:ask", (text) => {
    if (text) void chat.send(text, []);
  });

  const send = (opts?: SendOptions) => void chat.send(query.trim(), images.images, opts);

  function newChat() {
    chat.newChat();
    setQuery("");
    images.clear();
  }

  function cancelEdit() {
    chat.setEditingFromId(null);
    setQuery("");
    images.discard();
  }

  async function beginEdit(item: FeedItem) {
    chat.setEditingFromId(item.id);
    setQuery(item.body === IMAGE_QUESTION ? "" : item.body);
    images.discard();
    images.set(await chatImagesToPending(item.images));
    queryRef.current?.focus();
  }

  // Inline edit (ChatGPT-style): resend the edited text as if this message replaced the original.
  function editAndResend(item: FeedItem, text: string) {
    if (text.trim()) void chat.send(text.trim(), [], { editFromId: item.id });
  }

  async function retry() {
    const last = chat.lastUserMessage();
    if (!last || chat.running) return;
    const lastImages = await chatImagesToPending(last.images);
    await chat.send(last.body === IMAGE_QUESTION ? "" : last.body, lastImages, { retry: true });
  }

  // "Try again, better" after a 👎: regenerate the last answer, steering it with the user's reason.
  async function regenerate(reason: string) {
    const last = chat.lastUserMessage();
    if (!last || chat.running || !reason.trim()) return;
    const lastImages = await chatImagesToPending(last.images);
    await chat.send(last.body === IMAGE_QUESTION ? "" : last.body, lastImages, { retry: true, refine: reason.trim() });
  }

  function onConfirm(commandId: string, approved: boolean) {
    const jobId = chat.activeJobId;
    if (!jobId) return;
    chat.setFeed((prev) => markConfirmAnswered(prev, commandId, approved ? "run" : "cancel"));
    void confirmCommand(jobId, commandId, approved).catch(() => undefined);
  }

  async function deleteThread(thread: ChatThread) {
    if (!confirm(`Delete chat “${thread.title}”? Related messages will be hidden.`)) return;
    try {
      if (await history.removeThread(thread)) chat.clear();
    } catch (err) {
      alert(errorText(err));
    }
  }

  return {
    auth, health, models, specialists, history, images, chat,
    query, setQuery, queryRef,
    runMode, setRunMode, imageProfile, setImageProfile, editStrength, setEditStrength,
    deep,
    toggleDeep: () => setDeep((v) => !v),
    // Lead with the original question (keeps a web search clean); the approach/choices steer the answer.
    pickOption: (origQuery: string, card: { title: string; hint: string }) =>
      void chat.send(`${origQuery} — ${card.title}`, [], { plain: true }),
    submitClarify: (origQuery: string, picks: { question: string; chosen: string[] }[]) => {
      const values = picks.flatMap((p) => p.chosen);
      void chat.send(values.length ? `${origQuery} — ${values.join(", ")}` : origQuery, [], { plain: true });
    },
    // a follow-up suggestion is a fresh question — route it normally (web/clarify as usual)
    pickSuggestion: (q: string) => void chat.send(q, []),
    canStop: Boolean(chat.activeJobId && chat.running),
    stop: () => void chat.stop(),
    send, newChat, cancelEdit, beginEdit, editAndResend, retry, regenerate, onConfirm, deleteThread,
  };
}

export type AppValue = ReturnType<typeof useAppState>;

const AppContext = createContext<AppValue | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  return <AppContext.Provider value={useAppState()}>{children}</AppContext.Provider>;
}

export function useApp(): AppValue {
  const value = useContext(AppContext);
  if (!value) throw new Error("useApp must be used inside <AppProvider>");
  return value;
}
