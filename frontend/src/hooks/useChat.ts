import { useEffect, useRef, useState } from "react";

import { cancelJob, createJob, getJobHistory, softDeleteJobs, streamJobEvents, type ImageForceIntent, type ImageProfile } from "@/lib/api";
import {
  attachmentsFromJob,
  conversationFromFeed,
  errorText,
  fileToBase64,
  IMAGE_QUESTION,
  strengthValue,
  titleFromQuery,
  type EditStrength,
  type PendingImage,
} from "@/lib/chat";
import {
  appendUserMessage,
  buildFeedFromEvents,
  mergeArtifactsFromJob,
  reduceFeed,
  STATUS_ID,
  upsertStatus,
} from "@/lib/feedReducer";
import { newId } from "@/lib/id";
import type { ChatThread } from "@/lib/threads";
import type { ChatImage, FeedItem, JobEvent, RunMode } from "@/lib/types";
import type { HistoryApi } from "@/hooks/useHistory";

/** What every new job is sent with (the chat bar's current settings). */
export type JobOptions = {
  mode: RunMode;
  specialist: string;
  specialistLabel: string;
  model: string;
  allowOverflow: boolean;
  imageProfile: ImageProfile;
  editStrength: EditStrength;
  /** Deep research toggle: research the topic across many sources */
  deep: boolean;
};

export type SendOptions = {
  retry?: boolean;
  forceIntent?: ImageForceIntent;
  plain?: boolean;
  options?: boolean;
  editFromId?: string;
};

/**
 * The open conversation: its messages (feed), the running job, sending, streaming and Stop.
 * `options` are read when a message is sent; `onSent` clears the composer.
 */
export function useChat(history: HistoryApi, options: JobOptions, onSent: () => void) {
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [title, setTitle] = useState("New chat");
  const [running, setRunning] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [editingFromId, setEditingFromId] = useState<string | null>(null);
  // Always points at the chat currently on screen. A running stream reads this to tell whether the user
  // has navigated to another chat: if so, its events must NOT be written into the now-different feed (they
  // would pollute that other chat). The job keeps running server-side; its answer is rebuilt from history
  // when the user reopens the chat it belongs to.
  const openThreadIdRef = useRef<string | null>(history.activeThreadId);
  useEffect(() => {
    openThreadIdRef.current = history.activeThreadId;
  }, [history.activeThreadId]);

  function applyEvent(event: JobEvent) {
    setFeed((prev) => reduceFeed(prev, event));
  }

  /** Empty chat (after deleting the open one, or on logout). */
  function clear() {
    setFeed([]);
    setTitle("New chat");
    setActiveJobId(null);
  }

  function newChat() {
    const id = newId();
    history.noteThread(id, "New chat", history.projectId);
    history.selectThread(id);
    clear();
    setEditingFromId(null);
    setRunning(false);
  }

  /** Rebuild a saved chat from its jobs' event histories. */
  async function openThread(thread: ChatThread) {
    history.selectThread(thread.id);
    setTitle(thread.title);
    setActiveJobId(thread.jobIds[thread.jobIds.length - 1] || null);
    const count = thread.jobIds.length;
    setFeed([
      {
        id: `loading-${thread.id}`,
        kind: "system",
        title: "Loading",
        body: count ? `Fetching ${count} message${count === 1 ? "" : "s"}…` : "Empty chat",
      },
    ]);
    try {
      let built: FeedItem[] = [];
      const errors: string[] = [];
      for (const jobId of thread.jobIds) {
        try {
          const saved = await getJobHistory(jobId);
          const images = await attachmentsFromJob(saved.job);
          const slice = mergeArtifactsFromJob(
            buildFeedFromEvents(saved.events, saved.job.query, images).filter((item) => item.id !== STATUS_ID),
            saved.job,
          );
          built = [
            ...built,
            ...slice.map((item) =>
              item.kind === "user"
                ? { ...item, meta: `${saved.job.mode} · ${saved.job.status}` }
                : { ...item, jobId: item.jobId || saved.job.id },
            ),
          ];
        } catch (err) {
          const job = history.jobById.get(jobId);
          errors.push(errorText(err));
          if (job) built = appendUserMessage(built, job.query, `${job.mode} · ${job.status} · history unavailable`);
        }
      }
      if (built.length === 0 && count === 0) {
        setFeed([]);
      } else if (errors.length && built.length === 0) {
        setFeed([{ id: `err-${thread.id}`, kind: "system", title: "Could not load chat", body: errors[0], meta: "error" }]);
      } else {
        setFeed(built);
      }
    } catch (err) {
      setFeed([{ id: `err-${thread.id}`, kind: "system", title: "Error", body: errorText(err), meta: "error" }]);
    }
  }

  /** Send a message (text and/or images) as a new job, then stream its events into the feed. */
  async function send(text: string, images: PendingImage[], opts: SendOptions = {}) {
    if (running) return;
    const trimmed = text.trim();
    const force = opts.forceIntent;
    if (!trimmed && images.length === 0) return;
    if (force === "describe" && images.length === 0) return alert("Attach an image to Describe.");
    if (force === "generate" && !trimmed) return alert("Type a prompt for Generate.");
    if ((force === "edit" || force === "inpaint") && images.length === 0) {
      return alert("Attach an image to Edit / Inpaint.");
    }
    let threadId = history.activeThreadId;
    const titleSeed = trimmed || images[0]?.file.name || "Image";
    const isNewThread = !threadId;
    if (!threadId) {
      threadId = newId();
      history.noteThread(threadId, titleFromQuery(titleSeed), history.projectId);
      history.selectThread(threadId);
    }
    // We are on this chat at send time; mark it now so the stream guard is correct even before the
    // activeThreadId effect flushes (the effect then keeps the ref current if the user switches away).
    openThreadIdRef.current = threadId;
    // Inline edit: replace this message and everything after it, dropping those turns' jobs.
    let baseFeed = feed;
    if (opts.editFromId) {
      const idx = feed.findIndex((f) => f.id === opts.editFromId);
      if (idx >= 0) {
        baseFeed = feed.slice(0, idx);
        const dropIds = [...new Set(feed.slice(idx).map((f) => f.jobId).filter(Boolean))] as string[];
        if (dropIds.length) {
          void softDeleteJobs(dropIds).catch(() => undefined);
          history.dropJobs(dropIds);
        }
      }
    }
    const chatImages: ChatImage[] = images.map((img) => ({ url: img.url, filename: img.file.name, mime: img.mime }));
    setRunning(true);
    setEditingFromId(null);
    setTitle(titleFromQuery(titleSeed));
    if (opts.retry) {
      setFeed((prev) => upsertStatus(prev, "Regenerating", "Sending the last query again…"));
    } else {
      setFeed((prev) =>
        appendUserMessage(
          opts.editFromId ? baseFeed : prev,
          trimmed || IMAGE_QUESTION,
          `${options.mode} · ${options.specialistLabel}`,
          chatImages,
        ),
      );
    }
    onSent();

    try {
      const payloadImages = await Promise.all(
        images.map(async (img) => ({ filename: img.file.name, mime: img.mime, data: await fileToBase64(img.file) })),
      );
      const job = await createJob({
        query: trimmed || (force === "describe" ? IMAGE_QUESTION : ""),
        mode: force === "doc_gen" ? "agentic" : force ? "chat" : options.mode,
        specialist: options.specialist,
        model: options.model || undefined,
        allow_overflow: options.allowOverflow,
        images: payloadImages,
        conversation: conversationFromFeed(baseFeed),
        force_intent: force,
        image_profile: options.imageProfile,
        image_strength: force === "edit" || force === "inpaint" ? strengthValue(options.editStrength) : undefined,
        // tag the job with its chat so history can regroup the conversation after a reload/login
        thread_id: threadId,
        // Deep / Options toggles — skipped for one-shot composer actions (force) and for a plain
        // send (opts.plain), e.g. when the user clicks an approach card and wants the full answer.
        depth: options.deep && !force && !opts.plain ? "deep" : undefined,
        options: opts.options && !force && !opts.plain ? true : undefined,
        // a plain send (picking an approach card / submitting a clarify form) routes normally — web
        // search still available — but marked a follow-up so the model won't ask to clarify again.
        followup: opts.plain ? true : undefined,
      });
      setActiveJobId(job.id);
      history.addJob(job);          // keep this chat's job in state now, so switching away & back won't drop it
      // the job carries thread_id, so history regroups it and the server keeps the chat's metadata;
      // a new chat started inside a project joins that project.
      if (isNewThread && history.projectId) history.moveToProject(threadId, history.projectId);
      setFeed((prev) => upsertStatus(prev, "Queued", `${job.status} · ${job.id.slice(0, 8)}…`));
      const streamThreadId = threadId;
      await streamJobEvents(job.id, (event) => {
        // the user switched to another chat mid-stream — drop this job's events instead of writing them
        // into whatever feed is now on screen; the finished answer is rebuilt from history on reopen.
        if (openThreadIdRef.current !== streamThreadId) return;
        applyEvent(event);
      });
      await history.refreshJobs();
    } catch (err) {
      setFeed((prev) => upsertStatus(prev, "Error", errorText(err), true));
    } finally {
      setRunning(false);
    }
  }

  /** Stop: the server ends the model call at its next word; the stream then reports "Stopped". */
  async function stop() {
    if (!activeJobId) return;
    try {
      await cancelJob(activeJobId);
    } catch {
      // the job finished in the same moment: nothing to stop
    }
  }

  const lastUserMessage = () => [...feed].reverse().find((item) => item.kind === "user");

  return {
    feed,
    setFeed,
    title,
    running,
    activeJobId,
    editingFromId,
    setEditingFromId,
    clear,
    newChat,
    openThread,
    send,
    stop,
    lastUserMessage,
  };
}

export type ChatApi = ReturnType<typeof useChat>;
