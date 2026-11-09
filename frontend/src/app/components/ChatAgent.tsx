"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState, type DragEvent, type FormEvent } from "react";

import { ApiError } from "@/lib/api";
import { useApi } from "@/lib/auth";
import { errorMessage } from "@/lib/hooks";
import type { ChatFilter, ChatSource, DocumentStatus } from "@/lib/types";
import { useVault } from "@/lib/vault-context";

export const UPLOAD_POLL_MS = 2_000;
const MAX_POLLS = 90;
const TERMINAL: ReadonlySet<DocumentStatus> = new Set([
  "completed",
  "review_required",
  "failed",
  "rejected",
]);

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  filter?: ChatFilter;
  sources?: ChatSource[];
  state: "streaming" | "done" | "error" | "stopped";
}

interface Attachment {
  threadId: string;
  filename: string;
  status: DocumentStatus | "uploading" | "error";
  detail?: string;
}

export function filterLabel(filter: ChatFilter): string {
  return `Context Filtered: ${filter.member ?? "All members"} | ${filter.category ?? "All categories"}`;
}

let idCounter = 0;
const nextId = () => `m${++idCounter}`;

function StatusPill({ status }: { status: Attachment["status"] }) {
  const styles: Record<string, string> = {
    uploading: "bg-slate-100 text-slate-600",
    processing: "bg-blue-50 text-blue-700",
    completed: "bg-emerald-50 text-emerald-700",
    review_required: "bg-amber-50 text-amber-800",
    failed: "bg-red-50 text-red-700",
    rejected: "bg-red-50 text-red-700",
    error: "bg-red-50 text-red-700",
  };
  const labels: Record<string, string> = {
    uploading: "Uploading…",
    processing: "Processing…",
    completed: "Added to vault",
    review_required: "Needs review",
    failed: "Failed",
    rejected: "Rejected",
    error: "Upload failed",
  };
  return <span className={`rounded-full px-2 py-0.5 text-xs ${styles[status]}`}>{labels[status]}</span>;
}

export function ChatAgent({ uploadPollMs = UPLOAD_POLL_MS }: { uploadPollMs?: number }) {
  const api = useApi();
  const { data: me } = useVault();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [dragging, setDragging] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const unmounted = useRef(false);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const conversationId = `chat-${useId()}`;
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    unmounted.current = false;
    const pending = timers.current;
    return () => {
      unmounted.current = true;
      abortRef.current?.abort();
      pending.forEach(clearTimeout);
    };
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo?.({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  const patchMessage = (id: string, patch: (m: ChatMessage) => ChatMessage) =>
    setMessages((list) => list.map((m) => (m.id === id ? patch(m) : m)));

  async function send(event?: FormEvent) {
    event?.preventDefault();
    const text = input.trim();
    if (!text || streaming) return;
    const assistantId = nextId();
    setMessages((list) => [
      ...list,
      { id: nextId(), role: "user", text, state: "done" },
      { id: assistantId, role: "assistant", text: "", state: "streaming" },
    ]);
    setInput("");
    setStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      for await (const evt of api.chat(text, conversationId, controller.signal)) {
        if (evt.event === "filter") patchMessage(assistantId, (m) => ({ ...m, filter: evt.data }));
        else if (evt.event === "token")
          patchMessage(assistantId, (m) => ({ ...m, text: m.text + evt.data.text }));
        else if (evt.event === "sources")
          patchMessage(assistantId, (m) => ({ ...m, sources: evt.data }));
        else if (evt.event === "error")
          patchMessage(assistantId, (m) => ({ ...m, text: evt.data.message, state: "error" }));
      }
      patchMessage(assistantId, (m) => (m.state === "streaming" ? { ...m, state: "done" } : m));
    } catch (error) {
      if (controller.signal.aborted) {
        patchMessage(assistantId, (m) => ({ ...m, state: "stopped" }));
      } else {
        const msg =
          error instanceof ApiError && error.status === 429
            ? "You're sending messages too quickly. Please wait a minute."
            : errorMessage(error);
        patchMessage(assistantId, (m) => ({ ...m, text: msg, state: "error" }));
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }

  const wait = (ms: number) =>
    new Promise<void>((resolve) => {
      const timer = setTimeout(() => {
        timers.current.delete(timer);
        resolve();
      }, ms);
      timers.current.add(timer);
    });

  async function watchUpload(threadId: string) {
    for (let attempt = 0; attempt < MAX_POLLS; attempt++) {
      await wait(uploadPollMs);
      if (unmounted.current) return;
      try {
        const status = await api.uploadStatus(threadId);
        setAttachments((list) =>
          list.map((a) =>
            a.threadId === threadId
              ? { ...a, status: status.status, detail: status.error ?? status.document_name }
              : a,
          ),
        );
        if (TERMINAL.has(status.status)) return;
      } catch {
        // Transient (e.g. cold start); keep polling until MAX_POLLS.
      }
    }
  }

  async function attach(files: File[]) {
    if (!files.length || !me) return;
    const placeholders = files.map((f, i) => ({
      threadId: `local-${attachments.length + i}-${f.name}`,
      filename: f.name,
      status: "uploading" as const,
    }));
    setAttachments((list) => [...list, ...placeholders]);
    try {
      const uploads = await api.upload(files, me.member.id, "chat");
      setAttachments((list) =>
        list.map((a) => {
          const index = placeholders.findIndex((p) => p.threadId === a.threadId);
          if (index === -1) return a;
          const upload = uploads[index];
          return upload ? { threadId: upload.thread_id, filename: upload.filename, status: "processing" } : a;
        }),
      );
      uploads.forEach((u) => void watchUpload(u.thread_id));
    } catch (error) {
      const detail = errorMessage(error);
      setAttachments((list) =>
        list.map((a) =>
          placeholders.some((p) => p.threadId === a.threadId) ? { ...a, status: "error", detail } : a,
        ),
      );
    }
  }

  function onDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    setDragging(false);
    void attach(Array.from(event.dataTransfer.files));
  }

  return (
    <section
      aria-label="Vault assistant"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      data-dragging={dragging}
      className={`enterprise-panel flex h-[560px] flex-col p-0 sm:p-0 ${
        dragging ? "border-indigo-500 ring-2 ring-indigo-200" : "border-slate-200"
      }`}
    >
      <header className="border-b border-slate-200 px-5 py-4 sm:px-6">
        <h2 className="section-title">Ask your vault</h2>
        <p className="text-xs text-slate-500">
          e.g. “When does John&apos;s passport expire?” · drop files here to add them
        </p>
      </header>

      <div
        ref={scrollRef}
        className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4 sm:px-6"
        aria-live="polite"
        aria-relevant="additions text"
        role="log"
      >
        {messages.length === 0 ? (
          <p className="text-sm text-slate-500">No messages yet.</p>
        ) : null}
        {messages.map((m) => (
          <div key={m.id} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
            <div
              data-role={m.role}
              className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm ${
                m.role === "user"
                  ? "bg-indigo-600 text-white"
                  : m.state === "error"
                    ? "bg-red-50 text-red-700"
                    : "bg-slate-100 text-slate-800"
              }`}
            >
              {m.filter ? (
                <p
                  data-testid="context-filter"
                  className="mb-1 inline-block rounded bg-white/70 px-1.5 py-0.5 font-mono text-[11px] text-indigo-700"
                >
                  {filterLabel(m.filter)}
                </p>
              ) : null}
              <p className="whitespace-pre-wrap" data-testid={m.role === "assistant" ? "assistant-text" : undefined}>
                {m.text || (m.state === "streaming" ? "…" : "")}
              </p>
              {m.state === "stopped" ? <p className="mt-1 text-xs text-slate-500">Stopped.</p> : null}
              {m.sources && m.sources.length > 0 ? (
                <ul className="mt-2 flex flex-wrap gap-1" aria-label="Sources">
                  {m.sources.map((s) => (
                    <li
                      key={s.document_id}
                      className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-600"
                    >
                      {s.document_name} · {s.category}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          </div>
        ))}
      </div>

      {attachments.length > 0 ? (
        <ul className="space-y-1 border-t border-slate-100 px-4 py-2" aria-label="Attachments">
          {attachments.map((a) => (
            <li key={a.threadId} className="flex items-center justify-between gap-2 text-xs">
              <span className="truncate">{a.filename}</span>
              <span className="flex items-center gap-2">
                <StatusPill status={a.status} />
                {a.status === "review_required" ? (
                  <Link href="/reviews" className="text-indigo-600 underline">
                    Review
                  </Link>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      <form onSubmit={send} className="flex items-end gap-3 border-t border-slate-200 p-4 sm:p-5">
        <label htmlFor="chat-input" className="sr-only">
          Message
        </label>
        <textarea
          id="chat-input"
          rows={2}
          value={input}
          maxLength={2000}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          placeholder="Ask about your family's documents…"
          className="min-w-0 flex-1 resize-none rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
        />
        {streaming ? (
          <button
            type="button"
            onClick={() => abortRef.current?.abort()}
            className="rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700"
          >
            Stop
          </button>
        ) : (
          <button
            type="submit"
            disabled={!input.trim()}
            className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            Send
          </button>
        )}
      </form>
    </section>
  );
}
