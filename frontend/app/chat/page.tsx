"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MarkdownMessage } from "@/components/markdown-message";
import { apiFetch, apiUrl } from "@/lib/api";
import { consumeSseStream, ParsedSseEvent } from "@/lib/sse";
import type {
  ChatMessage,
  ChatSession,
  PendingApproval,
  PersistedChatMessage,
  ResourceItem,
  TimelineStep,
} from "@/lib/types";

function id(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

function groupSessions(sessions: ChatSession[]) {
  const now = Date.now();
  const day = 24 * 60 * 60 * 1000;
  return {
    "Hôm nay": sessions.filter((session) => now - new Date(session.created_at).getTime() < day),
    "Tuần này": sessions.filter((session) => {
      const age = now - new Date(session.created_at).getTime();
      return age >= day && age < day * 7;
    }),
    "Cũ hơn": sessions.filter((session) => now - new Date(session.created_at).getTime() >= day * 7),
  };
}

function stepTone(status: string) {
  if (["completed", "success", "done"].includes(status)) return "bg-status-sage text-white";
  if (["failed", "error"].includes(status)) return "bg-status-crimson text-white";
  if (["running", "pending"].includes(status)) return "border-accent-active bg-main-background";
  return "border-warm-border bg-surface-card";
}

function ResourceIcon({ kind }: { kind: ResourceItem["kind"] }) {
  if (kind === "ssh") return <span aria-hidden>⌁</span>;
  if (kind === "clickhouse") return <span aria-hidden>▦</span>;
  return <span aria-hidden>▣</span>;
}

function Sidebar({
  sessions,
  activeSessionId,
  onCreate,
  onSelect,
  onDelete,
}: {
  sessions: ChatSession[];
  activeSessionId: string | null;
  onCreate: () => void;
  onSelect: (sessionId: string) => void;
  onDelete: (sessionId: string) => void;
}) {
  const grouped = groupSessions(sessions);

  return (
    <aside className="hidden h-screen w-[260px] shrink-0 flex-col border-r border-warm-border bg-surface-card md:flex">
      <div className="border-b border-warm-border p-3">
        <button
          type="button"
          onClick={onCreate}
          className="flex h-10 w-full items-center justify-center gap-2 rounded-lg border border-warm-border bg-surface-card text-sm font-medium transition hover:border-accent-active hover:bg-main-background"
        >
          <span aria-hidden>+</span>
          Phiên hội thoại mới
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-3">
        {Object.entries(grouped).map(([label, items]) =>
          items.length ? (
            <section key={label} className="mb-5">
              <h2 className="px-2 pb-2 text-[11px] font-semibold uppercase text-secondary-text">
                {label}
              </h2>
              <div className="space-y-1">
                {items.map((session) => (
                  <div
                    key={session.id}
                    className={`group flex items-center gap-2 rounded-lg px-2 py-2 transition ${
                      activeSessionId === session.id
                        ? "bg-main-background text-primary-text"
                        : "text-secondary-text hover:bg-main-background/70 hover:text-primary-text"
                    } ${session.optimistic ? "opacity-70" : ""}`}
                  >
                    <button
                      type="button"
                      onClick={() => onSelect(session.id)}
                      className="min-w-0 flex-1 truncate text-left text-sm"
                    >
                      {session.title}
                    </button>
                    <button
                      type="button"
                      onClick={() => onDelete(session.id)}
                      aria-label={`Xóa ${session.title}`}
                      className="rounded p-1 text-secondary-text opacity-0 transition hover:bg-code-block hover:text-status-crimson group-hover:opacity-100"
                    >
                      ♲
                    </button>
                  </div>
                ))}
              </div>
            </section>
          ) : null,
        )}
      </div>
      <nav className="grid grid-cols-2 gap-2 border-t border-warm-border p-3 text-xs">
        <Link className="rounded-md px-2 py-2 text-center hover:bg-main-background" href="/admin/skills">
          Skills
        </Link>
        <Link
          className="rounded-md px-2 py-2 text-center hover:bg-main-background"
          href="/admin/skills/upload"
        >
          Upload
        </Link>
      </nav>
    </aside>
  );
}

function ApprovalCard({
  approval,
  onResolve,
}: {
  approval: PendingApproval;
  onResolve: (action: "approve" | "reject", note: string) => void;
}) {
  const [note, setNote] = useState("");
  const resolved = approval.status !== "pending";

  return (
    <div
      className={`rounded-lg border p-4 ${
        resolved
          ? "border-status-sage bg-status-sage/5"
          : "border-accent-active bg-[#fff8eb]"
      }`}
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-bold uppercase text-primary-text">
            PHÊ DUYỆT TÁC VỤ HẠ TẦNG CỐT LÕI
          </h3>
          <p className="mt-1 text-sm text-secondary-text">
            Agent đang yêu cầu quyền tiếp tục một tác vụ có rủi ro ghi/đổi trạng thái.
          </p>
        </div>
        <span className="rounded-md bg-code-block px-2 py-1 font-mono text-[11px] text-secondary-text">
          {approval.status}
        </span>
      </div>
      <dl className="grid gap-2 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-[11px] uppercase text-secondary-text">Approval ID</dt>
          <dd className="truncate font-mono">{approval.approval_request_id}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase text-secondary-text">Run</dt>
          <dd className="truncate font-mono">{approval.run_id ?? "streaming-run"}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase text-secondary-text">Lý do AI</dt>
          <dd>{approval.reason}</dd>
        </div>
      </dl>
      <textarea
        value={resolved ? approval.note ?? "" : note}
        disabled={resolved}
        onChange={(event) => setNote(event.target.value)}
        placeholder="Ghi chú của người trực ca..."
        className="mt-4 min-h-20 w-full resize-none rounded-lg border border-warm-border bg-surface-card p-3 text-sm disabled:bg-main-background"
      />
      {!resolved ? (
        <div className="mt-3 grid grid-cols-2 gap-3">
          <button
            type="button"
            onClick={() => onResolve("reject", note)}
            className="h-11 rounded-lg bg-code-block text-sm font-semibold text-primary-text transition hover:bg-warm-border"
          >
            Từ chối lệnh
          </button>
          <button
            type="button"
            onClick={() => onResolve("approve", note)}
            className="h-11 rounded-lg bg-accent-active text-sm font-semibold text-white transition hover:bg-[#b86205]"
          >
            Xác nhận cho chạy
          </button>
        </div>
      ) : null}
    </div>
  );
}

function TimelinePanel({ steps }: { steps: TimelineStep[] }) {
  if (!steps.length) {
    return (
      <div className="rounded-lg border border-dashed border-warm-border p-4 text-sm text-secondary-text">
        Chưa có bước LangGraph. Khi run bắt đầu, timeline sẽ sáng từng node ở đây.
      </div>
    );
  }

  return (
    <ol className="ml-2 border-l-2 border-warm-border pl-4">
      {steps.map((step) => (
        <li key={step.id} className="relative pb-4">
          <span
            className={`absolute -left-[25px] top-0 flex h-4 w-4 items-center justify-center rounded-full border text-[9px] ${stepTone(
              step.status,
            )}`}
          >
            {step.status === "running" ? (
              <span className="h-2 w-2 animate-spin rounded-full border border-accent-active border-t-transparent" />
            ) : step.status === "completed" ? (
              "✓"
            ) : (
              ""
            )}
          </span>
          <details className="rounded-lg border border-warm-border bg-surface-card p-3">
            <summary className="cursor-pointer text-sm font-medium">
              {step.step_index + 1}. {step.name}
              <span className="ml-2 font-mono text-[11px] text-secondary-text">{step.status}</span>
            </summary>
            <pre className="mt-3 overflow-x-auto rounded bg-code-block p-3 font-mono text-xs leading-5 text-primary-text">
              {JSON.stringify(step, null, 2)}
            </pre>
          </details>
        </li>
      ))}
    </ol>
  );
}

function ResourcePanel({ resources }: { resources: ResourceItem[] }) {
  if (!resources.length) {
    return (
      <div className="rounded-lg border border-dashed border-warm-border p-4 text-sm text-secondary-text">
        Chưa đọc được inventory từ backend. Kiểm tra `/api/v1/resources` hoặc cấu hình connector.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {resources.map((resource) => (
        <div
          key={resource.id}
          className="grid grid-cols-[28px_1fr_auto] items-center gap-2 rounded-lg border border-warm-border bg-surface-card px-3 py-2"
        >
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-code-block text-sm">
            <ResourceIcon kind={resource.kind} />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span
                className={`h-2 w-2 rounded-full ${
                  resource.status === "connected" ? "bg-status-sage" : "bg-status-crimson"
                }`}
              />
              <p className="truncate font-mono text-xs">{resource.name}</p>
            </div>
            <p className="mt-0.5 truncate text-[11px] text-secondary-text">{resource.region}</p>
          </div>
          <span
            className={`rounded px-2 py-1 text-[11px] font-medium ${
              resource.access === "read_write"
                ? "bg-accent-active/10 text-accent-active"
                : "bg-code-block text-secondary-text"
            }`}
          >
            {resource.access === "read_write" ? "Read-Write" : "Read-Only"}
          </span>
        </div>
      ))}
    </div>
  );
}

function RightRail({ steps, resources, activeTab, setActiveTab }: {
  steps: TimelineStep[];
  resources: ResourceItem[];
  activeTab: "timeline" | "resources";
  setActiveTab: (tab: "timeline" | "resources") => void;
}) {
  return (
    <aside className="hidden h-screen w-[360px] shrink-0 flex-col border-l border-warm-border bg-main-background p-4 xl:flex">
      <div className="grid grid-cols-2 rounded-lg border border-warm-border bg-surface-card p-1">
        <button
          type="button"
          onClick={() => setActiveTab("timeline")}
          className={`rounded-md px-2 py-2 text-xs font-semibold ${
            activeTab === "timeline" ? "bg-code-block text-primary-text" : "text-secondary-text"
          }`}
        >
          Mạch tư duy
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("resources")}
          className={`rounded-md px-2 py-2 text-xs font-semibold ${
            activeTab === "resources" ? "bg-code-block text-primary-text" : "text-secondary-text"
          }`}
        >
          Tài nguyên
        </button>
      </div>
      <div className="mt-4 min-h-0 flex-1 overflow-y-auto">
        {activeTab === "timeline" ? (
          <TimelinePanel steps={steps} />
        ) : (
          <ResourcePanel resources={resources} />
        )}
      </div>
    </aside>
  );
}

export default function ChatPage() {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [steps, setSteps] = useState<TimelineStep[]>([]);
  const [resources, setResources] = useState<ResourceItem[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"timeline" | "resources">("timeline");
  const [approval, setApproval] = useState<PendingApproval | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const historyRequestRef = useRef(0);

  const resourceQuery = useMemo(() => {
    const match = prompt.match(/(^|\s)@([\w.-]*)$/);
    return match?.[2] ?? null;
  }, [prompt]);

  const resourceOptions = useMemo(() => {
    if (resourceQuery === null) return [];
    return resources.filter(
      (resource) =>
        resource.status === "connected" &&
        resource.name.toLowerCase().includes(resourceQuery.toLowerCase()),
    );
  }, [resourceQuery, resources]);

  const loadSessionMessages = useCallback(async (sessionId: string) => {
    const requestId = historyRequestRef.current + 1;
    historyRequestRef.current = requestId;
    setActiveSessionId(sessionId);
    setError(null);
    setApproval(null);
    setSteps([]);
    setMessages([]);

    if (sessionId.startsWith("session-")) {
      setLoadingMessages(false);
      setMessages([]);
      return;
    }

    setLoadingMessages(true);
    try {
      const history = await apiFetch<PersistedChatMessage[]>(`/sessions/${sessionId}/messages`);
      if (historyRequestRef.current !== requestId) return;
      setMessages(
        history
          .filter(
            (
              message,
            ): message is PersistedChatMessage & { role: "user" | "assistant" } =>
              message.role === "user" || message.role === "assistant",
          )
          .map((message) => ({
            id: message.id,
            role: message.role,
            content: message.content,
            status: message.status === "failed" ? "error" : "done",
          })),
      );
    } catch (exc) {
      if (historyRequestRef.current !== requestId) return;
      setMessages([]);
      setError(exc instanceof Error ? exc.message : "Không tải được lịch sử phiên chat.");
    } finally {
      if (historyRequestRef.current === requestId) setLoadingMessages(false);
    }
  }, []);

  useEffect(() => {
    apiFetch<ChatSession[]>("/sessions")
      .then((data) => {
        setSessions(data);
        if (data[0]) void loadSessionMessages(data[0].id);
      })
      .catch((exc: Error) => setError(exc.message));
    apiFetch<ResourceItem[]>("/resources")
      .then(setResources)
      .catch(() => setResources([]));
  }, [loadSessionMessages]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, approval]);

  function updateAssistant(messageId: string, updater: (message: ChatMessage) => ChatMessage) {
    setMessages((current) =>
      current.map((message) => (message.id === messageId ? updater(message) : message)),
    );
  }

  async function createSession() {
    historyRequestRef.current += 1;
    const optimisticId = id("session");
    const optimistic: ChatSession = {
      id: optimisticId,
      title: "New Session",
      created_at: new Date().toISOString(),
      optimistic: true,
    };
    setSessions((current) => [optimistic, ...current]);
    setActiveSessionId(optimisticId);
    setMessages([]);
    setLoadingMessages(false);
    setSteps([]);
    setApproval(null);

    try {
      const created = await apiFetch<{ session_id: string; title: string }>("/sessions", {
        method: "POST",
        body: JSON.stringify({ title: "New Session" }),
      });
      setSessions((current) =>
        current.map((session) =>
          session.id === optimisticId
            ? {
                id: created.session_id,
                title: created.title,
                created_at: optimistic.created_at,
              }
            : session,
        ),
      );
      setActiveSessionId(created.session_id);
      return created.session_id;
    } catch (exc) {
      setSessions((current) => current.filter((session) => session.id !== optimisticId));
      const fallbackSessionId = sessions[0]?.id ?? null;
      setActiveSessionId(fallbackSessionId);
      if (fallbackSessionId) void loadSessionMessages(fallbackSessionId);
      setError(exc instanceof Error ? exc.message : "Không tạo được session.");
      throw exc;
    }
  }

  async function deleteSession(sessionId: string) {
    setSessions((current) => current.filter((session) => session.id !== sessionId));
    if (activeSessionId === sessionId) {
      setActiveSessionId(null);
      setMessages([]);
      setSteps([]);
    }
    if (!sessionId.startsWith("session-")) {
      await apiFetch(`/sessions/${sessionId}`, { method: "DELETE" }).catch((exc: Error) =>
        setError(exc.message),
      );
    }
  }

  function handleSseEvent(event: ParsedSseEvent, assistantId: string) {
    const payload = event.data;
    if (event.event === "run_started") {
      setActiveTab("timeline");
      setStreaming(true);
    }
    if (event.event === "text_delta") {
      const delta = typeof payload.delta === "string" ? payload.delta : "";
      updateAssistant(assistantId, (message) => ({
        ...message,
        content: message.content + delta,
        status: "streaming",
      }));
    }
    if (event.event === "timeline_updated" && Array.isArray(payload.steps)) {
      setSteps(payload.steps as TimelineStep[]);
    }
    if (event.event === "run_suspended") {
      setStreaming(false);
      setApproval({
        approval_request_id: String(payload.approval_request_id ?? ""),
        run_id: typeof payload.run_id === "string" ? payload.run_id : undefined,
        reason: typeof payload.reason === "string" ? payload.reason : "Backend suspended run.",
        status: "pending",
      });
    }
    if (event.event === "run_completed") {
      const finalAnswer = typeof payload.final_answer === "string" ? payload.final_answer : "";
      updateAssistant(assistantId, (message) => ({
        ...message,
        content: message.content || finalAnswer,
        status: "done",
      }));
      setStreaming(false);
      apiFetch<ChatSession[]>("/sessions").then(setSessions).catch(() => undefined);
    }
    if (event.event === "run_failed" || event.event === "error") {
      const message = String(payload.error ?? payload.message ?? "Run failed.");
      updateAssistant(assistantId, (current) => ({
        ...current,
        content: current.content ? `${current.content}\n\n${message}` : message,
        status: "error",
      }));
      setStreaming(false);
    }
  }

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = prompt.trim();
    if (!content || streaming) return;

    setError(null);
    setApproval(null);
    const sessionId = activeSessionId?.startsWith("session-")
      ? await createSession()
      : activeSessionId ?? (await createSession());
    const userMessage: ChatMessage = { id: id("user"), role: "user", content, status: "done" };
    const assistantMessage: ChatMessage = {
      id: id("assistant"),
      role: "assistant",
      content: "",
      status: "streaming",
    };
    setMessages((current) => [...current, userMessage, assistantMessage]);
    setPrompt("");
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetch(apiUrl("/chat/stream"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, user_message: content }),
        signal: controller.signal,
      });
      await consumeSseStream(response, (sseEvent) => handleSseEvent(sseEvent, assistantMessage.id));
    } catch (exc) {
      if (controller.signal.aborted) {
        updateAssistant(assistantMessage.id, (message) => ({
          ...message,
          content: message.content || "Đã dừng stream theo yêu cầu người trực.",
          status: "done",
        }));
      } else {
        const message = exc instanceof Error ? exc.message : "Không mở được SSE stream.";
        setError(message);
        updateAssistant(assistantMessage.id, (current) => ({
          ...current,
          content: message,
          status: "error",
        }));
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }

  async function resolveApproval(action: "approve" | "reject", note: string) {
    if (!approval) return;
    setApproval({ ...approval, status: action === "approve" ? "approved" : "rejected", note });
    const assistantMessage: ChatMessage = {
      id: id("assistant"),
      role: "assistant",
      content: "",
      status: action === "approve" ? "streaming" : "done",
    };
    if (action === "reject") {
      setMessages((current) => [
        ...current,
        { ...assistantMessage, content: "Tác vụ đã bị từ chối và run được khóa tại điểm duyệt." },
      ]);
    } else {
      setMessages((current) => [...current, assistantMessage]);
    }

    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(action === "approve");

    try {
      const response = await fetch(apiUrl(`/approvals/${approval.approval_request_id}/resolve`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, note }),
        signal: controller.signal,
      });
      await consumeSseStream(response, (sseEvent) => handleSseEvent(sseEvent, assistantMessage.id));
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : "Không resolve được approval.";
      setError(message);
      updateAssistant(assistantMessage.id, (current) => ({
        ...current,
        content: current.content || message,
        status: "error",
      }));
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }

  function pickResource(resource: ResourceItem) {
    setPrompt((current) => current.replace(/(^|\s)@([\w.-]*)$/, `$1@${resource.name} `));
  }

  return (
    <main className="flex h-screen overflow-hidden bg-main-background text-primary-text">
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        onCreate={() => void createSession()}
        onSelect={(sessionId) => void loadSessionMessages(sessionId)}
        onDelete={(sessionId) => void deleteSession(sessionId)}
      />
      <section className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-warm-border bg-main-background px-5">
          <div>
            <h1 className="text-sm font-semibold">Telecom Agent Console</h1>
            <p className="text-xs text-secondary-text">SSE token stream · HITL approval · run timeline</p>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <span className="h-2 w-2 rounded-full bg-status-sage" />
            Backend stream ready
          </div>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-6">
          <div className="mx-auto flex max-w-4xl flex-col gap-5">
            {loadingMessages ? (
              <div
                aria-busy="true"
                aria-label="Đang tải lịch sử phiên chat"
                className="space-y-3"
              >
                <div className="h-16 animate-pulse rounded-lg bg-surface-card" />
                <div className="ml-auto h-12 w-2/3 animate-pulse rounded-lg bg-code-block" />
                <div className="h-24 w-5/6 animate-pulse rounded-lg bg-surface-card" />
              </div>
            ) : messages.length === 0 ? (
              <div className="rounded-lg border border-dashed border-warm-border bg-surface-card p-6">
                <h2 className="text-base font-semibold">Sẵn sàng nhận ca vận hành.</h2>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-secondary-text">
                  Gõ câu hỏi hoặc tag nhanh một tài nguyên bằng `@`. Khi backend trả về `text_delta`,
                  câu trả lời sẽ hiện theo từng chunk ngay trong luồng chat.
                </p>
              </div>
            ) : null}

            {messages.map((message) => (
              <article
                key={message.id}
                className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-[82%] rounded-lg border px-4 py-3 ${
                    message.role === "user"
                      ? "border-accent-active/30 bg-accent-active text-white"
                      : message.status === "error"
                        ? "border-status-crimson/40 bg-status-crimson/5"
                        : "border-warm-border bg-surface-card"
                  }`}
                >
                  <MarkdownMessage content={message.content} />
                </div>
              </article>
            ))}

            {approval ? <ApprovalCard approval={approval} onResolve={resolveApproval} /> : null}
            {error ? (
              <div role="alert" className="rounded-lg border border-status-crimson/40 bg-status-crimson/5 p-3 text-sm text-status-crimson">
                {error}
              </div>
            ) : null}
            <div ref={bottomRef} />
          </div>
        </div>
        <form onSubmit={sendMessage} className="shrink-0 border-t border-warm-border bg-main-background p-4">
          <div className="relative mx-auto max-w-4xl">
            {resourceOptions.length ? (
              <div className="absolute bottom-full left-3 mb-2 w-80 rounded-lg border border-warm-border bg-surface-card p-2 shadow-sm">
                {resourceOptions.map((resource) => (
                  <button
                    key={resource.id}
                    type="button"
                    onClick={() => pickResource(resource)}
                    className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm hover:bg-main-background"
                  >
                    <span className="flex h-6 w-6 items-center justify-center rounded bg-code-block">
                      <ResourceIcon kind={resource.kind} />
                    </span>
                    <span className="min-w-0 flex-1 truncate font-mono text-xs">{resource.name}</span>
                    <span className="h-2 w-2 rounded-full bg-status-sage" />
                  </button>
                ))}
              </div>
            ) : null}
            <div className="flex items-end gap-3 rounded-xl border border-warm-border bg-surface-card p-2">
              <textarea
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                disabled={streaming}
                rows={2}
                placeholder="Hỏi agent, ví dụ: kiểm tra lag trên @hanoi-core-01..."
                className="max-h-36 min-h-14 flex-1 resize-none border-0 bg-transparent px-2 py-3 text-sm outline-none placeholder:text-secondary-text disabled:cursor-not-allowed"
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
              />
              <button
                type={streaming ? "button" : "submit"}
                onClick={streaming ? () => abortRef.current?.abort() : undefined}
                className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-lg font-semibold text-white ${
                  streaming ? "bg-primary-text" : "bg-accent-active hover:bg-[#b86205]"
                }`}
                aria-label={streaming ? "Dừng stream" : "Gửi câu lệnh"}
              >
                {streaming ? "■" : "↗"}
              </button>
            </div>
          </div>
        </form>
      </section>
      <RightRail
        steps={steps}
        resources={resources}
        activeTab={activeTab}
        setActiveTab={setActiveTab}
      />
    </main>
  );
}
