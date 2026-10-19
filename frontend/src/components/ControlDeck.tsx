"use client";

/**
 * The chat app: wires the hooks (state + API calls) to the screen parts (components/chat/*).
 * Hooks:      useAuth · useSpecialists · useServers · useHistory · useChat · usePendingImages ·
 *             useModels · useHealth · useWindowEvent
 * Components: AuthScreen · ChatSidebar · ChatHeader · MessageFeed · Composer · SettingsDrawer ·
 *             SpecialistModal
 */
import { useCallback, useEffect, useRef, useState } from "react";

import AuthScreen from "@/components/chat/AuthScreen";
import ChatHeader from "@/components/chat/ChatHeader";
import ChatSidebar from "@/components/chat/ChatSidebar";
import Composer from "@/components/chat/Composer";
import MessageFeed from "@/components/chat/MessageFeed";
import SettingsDrawer from "@/components/chat/SettingsDrawer";
import SpecialistModal from "@/components/chat/SpecialistModal";
import { useAuth } from "@/hooks/useAuth";
import { useChat, type SendOptions } from "@/hooks/useChat";
import { useHealth } from "@/hooks/useHealth";
import { useHistory } from "@/hooks/useHistory";
import { useModels } from "@/hooks/useModels";
import { usePendingImages } from "@/hooks/usePendingImages";
import { useServers } from "@/hooks/useServers";
import { useSpecialists } from "@/hooks/useSpecialists";
import { useWindowEvent } from "@/hooks/useWindowEvent";
import type { ImageProfile } from "@/lib/api";
import { chatImagesToPending, errorText, IMAGE_QUESTION, type EditStrength } from "@/lib/chat";
import type { ChatThread } from "@/lib/threads";
import type { FeedItem, RunMode, Specialist } from "@/lib/types";

export default function ControlDeck() {
  const health = useHealth();
  const models = useModels();
  const specialists = useSpecialists();
  const history = useHistory();
  const images = usePendingImages();

  // the chat bar's settings
  const [query, setQuery] = useState("");
  const [runMode, setRunMode] = useState<RunMode>("auto");
  const [allowOverflow, setAllowOverflow] = useState(true);
  const [imageProfile, setImageProfile] = useState<ImageProfile>("fast");
  const [editStrength, setEditStrength] = useState<EditStrength>("medium");
  const [settingsOpen, setSettingsOpen] = useState(false);
  // the specialist window: undefined = closed, null = new specialist
  const [editingSpecialist, setEditingSpecialist] = useState<Specialist | null | undefined>(undefined);
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
    },
    () => {
      setQuery("");
      images.clear();
    },
  );

  // signing in loads the user's data; signing out forgets it (and the session's server logins)
  const forgetServers = useRef<() => void>(() => undefined);
  const { load: loadSpecialists, clear: clearSpecialists } = specialists;
  const { refresh: refreshModels } = models;
  const { refreshJobs, clear: clearJobs, selectThread } = history;
  const { setFeed } = chat;
  const onSignedIn = useCallback(
    async (fresh: boolean) => {
      await loadSpecialists();
      await refreshModels(fresh);
      await refreshJobs();
    },
    [loadSpecialists, refreshModels, refreshJobs],
  );
  const onSignedOut = useCallback(
    (reason: "logout" | "expired") => {
      if (reason === "logout") {
        forgetServers.current(); // while the token is still valid
        selectThread(null);
      }
      clearSpecialists();
      clearJobs();
      setFeed([]);
    },
    [clearSpecialists, clearJobs, selectThread, setFeed],
  );
  const auth = useAuth({ onSignedIn, onSignedOut });
  const isAdmin = auth.user?.role === "admin";

  const servers = useServers(settingsOpen && isAdmin);
  useEffect(() => {
    forgetServers.current = servers.forget;
  }, [servers.forget]);

  // A diagram that failed to draw asks the assistant to fix it (MermaidDiagram sends "multeagent:ask").
  useWindowEvent<string>("multeagent:ask", (text) => {
    if (text) void chat.send(text, []);
  });

  function send(opts?: SendOptions) {
    void chat.send(query.trim(), images.images, opts);
  }

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

  async function deleteThread(thread: ChatThread) {
    if (!confirm(`Delete chat “${thread.title}”? Related messages will be hidden.`)) return;
    try {
      if (await history.removeThread(thread)) chat.clear();
    } catch (err) {
      alert(errorText(err));
    }
  }

  if (!auth.user) {
    return <AuthScreen health={health.health} healthError={health.error} error={auth.error} onSignIn={auth.signIn} />;
  }

  const canStop = Boolean(chat.activeJobId && chat.running);
  const stop = () => void chat.stop();
  const subtitle = `${runMode} · ${specialists.label}${history.activeProject ? ` · ${history.activeProject.name}` : ""}`;

  return (
    <div className="chat-app">
      <ChatSidebar
        username={auth.user.username}
        history={history}
        health={health.health}
        onNewChat={newChat}
        onOpenThread={(thread) => void chat.openThread(thread)}
        onDeleteThread={(thread) => void deleteThread(thread)}
        onOpenSettings={() => setSettingsOpen(true)}
        onSignOut={auth.signOut}
      />

      <main className="chat-main">
        <ChatHeader
          title={chat.title}
          subtitle={subtitle}
          runMode={runMode}
          onRunMode={setRunMode}
          specialists={specialists.specialists}
          specialistChoice={specialists.choice}
          onSpecialistChoice={specialists.setChoice}
          models={models.models}
          modelOverride={models.override}
          onModelOverride={models.setOverride}
          canStop={canStop}
          onStop={stop}
        />
        <MessageFeed
          feed={chat.feed}
          running={chat.running}
          activeJobId={chat.activeJobId}
          onEdit={(item) => void beginEdit(item)}
          onRetry={() => void retry()}
        />
        <Composer
          query={query}
          onQuery={setQuery}
          inputRef={queryRef}
          images={images}
          editing={Boolean(chat.editingFromId)}
          onCancelEdit={cancelEdit}
          running={chat.running}
          canStop={canStop}
          onStop={stop}
          onSend={send}
          runMode={runMode}
          specialistLabel={specialists.label}
          onOpenSettings={() => setSettingsOpen(true)}
          imageProfile={imageProfile}
          onImageProfile={setImageProfile}
          editStrength={editStrength}
          onEditStrength={setEditStrength}
          imagePipeline={health.health?.image_pipeline}
        />
      </main>

      {settingsOpen ? (
        <SettingsDrawer
          isAdmin={isAdmin}
          specialists={specialists}
          servers={servers}
          allowOverflow={allowOverflow}
          onAllowOverflow={setAllowOverflow}
          onSyncModels={() => void models.refresh(true)}
          onNewSpecialist={() => setEditingSpecialist(null)}
          onEditSpecialist={setEditingSpecialist}
          onClose={() => setSettingsOpen(false)}
        />
      ) : null}

      {editingSpecialist !== undefined ? (
        <SpecialistModal
          key={editingSpecialist?.id ?? "new"}
          specialist={editingSpecialist}
          options={specialists.options}
          onSave={specialists.save}
          onDelete={specialists.remove}
          onClose={() => setEditingSpecialist(undefined)}
        />
      ) : null}
    </div>
  );
}
