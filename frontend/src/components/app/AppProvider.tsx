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

  async function retry() {
    const last = chat.lastUserMessage();
    if (!last || chat.running) return;
    const lastImages = await chatImagesToPending(last.images);
    await chat.send(last.body === IMAGE_QUESTION ? "" : last.body, lastImages, { retry: true });
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
    pickOption: (origQuery: string, card: { title: string; hint: string }) =>
      void chat.send(`Answer using the "${card.title}" approach — ${card.hint}.\n\nQuestion: ${origQuery}`, [], { plain: true }),
    submitClarify: (origQuery: string, picks: { question: string; chosen: string[] }[]) => {
      const chosen = picks.filter((p) => p.chosen.length).map((p) => `${p.question} → ${p.chosen.join(", ")}`).join("; ");
      void chat.send(chosen ? `${origQuery}\n\nMy choices: ${chosen}` : origQuery, [], { plain: true });
    },
    canStop: Boolean(chat.activeJobId && chat.running),
    stop: () => void chat.stop(),
    send, newChat, cancelEdit, beginEdit, retry, onConfirm, deleteThread,
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
