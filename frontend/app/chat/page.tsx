"use client";

import Link from "next/link";
import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ModelPicker, SkillCommandMenu } from "@/components/chat-config-menu";
import {
  DatabaseIcon,
  InterveneIcon,
  PlusIcon,
  SendIcon,
  ServerIcon,
  SkillsIcon,
  StopIcon,
  TerminalIcon,
  TrashIcon,
  UploadIcon,
  CloseIcon,
  TelecomLogo,
} from "@/components/icons";
import { MarkdownMessage } from "@/components/markdown-message";
import { apiFetch, apiUrl } from "@/lib/api";
import { consumeSseStream, ParsedSseEvent } from "@/lib/sse";
import { useRunTimeline } from "./use-run-timeline";
import type {
  ChatMessage,
  ChatModelOption,
  ChatOptions,
  ChatSession,
  ChatSkillOption,
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
  if (["running", "pending", "waiting_approval"].includes(status)) return "border-accent-active bg-main-background";
  return "border-warm-border bg-surface-card";
}

function isCompletedStatus(status: string) {
  return ["completed", "success", "done"].includes(status);
}

function isFailedStatus(status: string) {
  return ["failed", "error", "rejected"].includes(status);
}

function statusLabel(status: string) {
  if (status === "rejected") return "Từ chối";
  if (isCompletedStatus(status)) return "Xong";
  if (isFailedStatus(status)) return "Lỗi";
  if (status === "running") return "Đang chạy";
  if (status === "waiting_approval") return "Chờ duyệt";
  if (status === "pending") return "Đang chờ";
  return status;
}

function statusBadgeClass(status: string) {
  if (isCompletedStatus(status)) {
    return "border-status-sage/30 bg-status-sage/10 text-status-sage";
  }
  if (isFailedStatus(status)) {
    return "border-status-crimson/30 bg-status-crimson/10 text-status-crimson";
  }
  if (["running", "pending", "waiting_approval"].includes(status)) {
    return "border-accent-active/30 bg-accent-active/10 text-accent-active";
  }
  return "border-warm-border bg-code-block text-secondary-text";
}

function stepTypeLabel(step: TimelineStep) {
  if (step.step_type === "llm_call") return "Reason";
  if (step.step_type === "tool_call") return step.tool_name ?? "Tool";
  if (step.step_type === "approval") return "Approval";
  if (step.step_type === "error") return "Error";
  return step.step_type.replaceAll("_", " ");
}

function stepIcon(step: TimelineStep) {
  if (step.step_type === "llm_call") {
    return (
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5} aria-hidden>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
      </svg>
    );
  }
  if (step.step_type === "tool_call") {
    return (
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5} aria-hidden>
        <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    );
  }
  if (step.step_type === "approval") {
    return (
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5} aria-hidden>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
      </svg>
    );
  }
  if (step.step_type === "error") {
    return (
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5} aria-hidden>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
      </svg>
    );
  }
  return <span className="h-2 w-2 rounded-full bg-current" aria-hidden />;
}

function compactText(value: string, maxLength = 900) {
  const compacted = value.replace(/\s+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
  if (compacted.length <= maxLength) return compacted;
  return `${compacted.slice(0, maxLength).trimEnd()}\n...`;
}

function formatStepValue(value: unknown, maxLength = 900) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "string") return compactText(value, maxLength);
  try {
    return compactText(JSON.stringify(value, null, 2), maxLength);
  } catch {
    return compactText(String(value), maxLength);
  }
}

function getStepTitle(step: TimelineStep) {
  return step.display_title || step.name || step.tool_name || "Agent step";
}

type RunTimelineResponse = {
  run_id: string;
  status: string;
  model?: string | null;
  steps: TimelineStep[];
};

type PendingApprovalDetail = {
  approval_id: string;
  run_id: string;
  status: string;
  skill_details?: {
    skill_name?: string | null;
    arguments?: Record<string, unknown> | null;
    risk_level?: string | null;
  } | null;
};

const ACTIVE_RUN_STATUSES = new Set(["pending", "running", "waiting_approval"]);
const TERMINAL_ERROR_RUN_STATUSES = new Set(["failed", "cancelled", "timed_out"]);

function chatMessageFromPersisted(
  message: PersistedChatMessage & { role: "user" | "assistant" },
): ChatMessage {
  return {
    id: message.id,
    role: message.role,
    content: message.content,
    status: message.status === "failed" ? "error" : "done",
    run_id: message.run_id,
  };
}

function findRunMissingAssistant(messages: ChatMessage[]) {
  const assistantRunIds = new Set(
    messages
      .filter((message) => message.role === "assistant" && message.run_id)
      .map((message) => message.run_id as string),
  );

  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === "user" && message.run_id && !assistantRunIds.has(message.run_id)) {
      return message.run_id;
    }
  }

  return null;
}

function latestStepSummary(
  steps: TimelineStep[],
  predicate: (step: TimelineStep) => boolean,
) {
  for (let index = steps.length - 1; index >= 0; index -= 1) {
    const step = steps[index];
    if (predicate(step) && step.summary?.trim()) return step.summary.trim();
  }
  return null;
}

function timelineHasOpenStep(steps: TimelineStep[]) {
  return steps.some((step) => ACTIVE_RUN_STATUSES.has(step.status));
}

function recoveredAssistantFromTimeline(runId: string, timeline: RunTimelineResponse): ChatMessage {
  const finalSummary = latestStepSummary(
    timeline.steps,
    (step) => step.step_type === "llm_call" && isCompletedStatus(step.status),
  );
  const errorSummary = latestStepSummary(
    timeline.steps,
    (step) => isFailedStatus(step.status) || Boolean(step.is_error),
  );
  const lastSummary = latestStepSummary(timeline.steps, () => true);

  if (TERMINAL_ERROR_RUN_STATUSES.has(timeline.status)) {
    const recoveredContent = finalSummary
      ? `${finalSummary}

---

Run gặp lỗi sau khi sinh phản hồi: ${
          errorSummary || "không lưu được trạng thái hoàn tất."
        }`
      : errorSummary || lastSummary || "Run đã dừng trước khi lưu được phản hồi cuối.";

    return {
      id: `recovered-${runId}`,
      role: "assistant",
      content: recoveredContent,
      status: "error",
      run_id: runId,
    };
  }

  if (timeline.status === "waiting_approval") {
    return {
      id: `recovered-${runId}`,
      role: "assistant",
      content: "Run đang chờ phê duyệt trước khi tiếp tục.",
      status: "done",
      run_id: runId,
    };
  }

  if (ACTIVE_RUN_STATUSES.has(timeline.status) && timelineHasOpenStep(timeline.steps)) {
    return {
      id: `recovered-${runId}`,
      role: "assistant",
      content: finalSummary || "Run đang xử lý. Timeline đã được khôi phục sau khi tải lại trang.",
      status: "streaming",
      run_id: runId,
    };
  }

  return {
    id: `recovered-${runId}`,
    role: "assistant",
    content: finalSummary || lastSummary || "Run đã hoàn tất nhưng phản hồi cuối chưa được lưu vào lịch sử chat.",
    status: "done",
    run_id: runId,
  };
}

function approvalFromPendingDetail(detail: PendingApprovalDetail): PendingApproval {
  return {
    approval_request_id: detail.approval_id,
    run_id: detail.run_id,
    tool_name: detail.skill_details?.skill_name ?? null,
    tool_input: detail.skill_details?.arguments ?? null,
    risk_level: detail.skill_details?.risk_level ?? null,
    status: "pending",
  };
}

function getApprovalToolName(step: TimelineStep) {
  return step.tool_name || step.name.replace(/^Chờ phê duyệt:\s*/i, "") || "tool";
}

function getApprovalTitle(step: TimelineStep) {
  const toolName = getApprovalToolName(step);
  if (step.status === "waiting_approval") return `Chờ phê duyệt: ${toolName}`;
  if (step.tool_status === "rejected" || isFailedStatus(step.status)) return `Đã từ chối: ${toolName}`;
  return `Đã duyệt: ${toolName}`;
}

function getStepSummary(step: TimelineStep) {
  if (step.step_type === "llm_call") {
    return step.summary || (step.status === "running" ? "Agent đang đọc ngữ cảnh và chọn hành động tiếp theo." : "Agent đã hoàn tất lượt suy luận.");
  }
  if (step.step_type === "approval") {
    return step.summary || "Tác vụ cần người vận hành phê duyệt trước khi chạy.";
  }
  if (step.step_type === "tool_call") {
    if (step.status === "running") return "Đang gọi skill/tool và chờ kết quả.";
    if (step.status === "failed") return step.summary || "Tool trả lỗi.";
    return step.summary || "Tool đã trả kết quả cho agent.";
  }
  return step.summary || "Bước đã được ghi nhận trong timeline.";
}

function pickString(value: Record<string, unknown> | null | undefined, key: string) {
  const raw = value?.[key];
  return typeof raw === "string" && raw.trim() ? raw.trim() : null;
}

function formatCount(value: number) {
  return new Intl.NumberFormat("vi-VN").format(value);
}

function summarizeLoaderOutput(step: TimelineStep, rawOutput: string) {
  const skillName =
    pickString(step.tool_input, "skill_name") ??
    rawOutput.match(/<skill_content\s+name="([^"]+)"/)?.[1] ??
    "skill";
  const resourceCount = rawOutput.match(/<file>/g)?.length ?? 0;
  const lines = rawOutput.split("\n").filter(Boolean).length;
  const resourceText = resourceCount ? ` · ${formatCount(resourceCount)} file resource` : "";
  return `Đã nạp skill "${skillName}" · ${formatCount(rawOutput.length)} ký tự hướng dẫn · ${formatCount(lines)} dòng${resourceText}.`;
}

function summarizeSkillFileOutput(step: TimelineStep, rawOutput: string) {
  const skillName = pickString(step.tool_input, "skill_name");
  const filePath = pickString(step.tool_input, "file_path") ?? "resource";
  const binary = rawOutput.startsWith("data:");
  const size = binary ? `${formatCount(rawOutput.length)} ký tự data URL` : `${formatCount(rawOutput.length)} ký tự`;
  return `Đã đọc ${filePath}${skillName ? ` từ skill "${skillName}"` : ""} · ${size}.`;
}

function formatToolOutput(step: TimelineStep) {
  if (step.tool_output === null || step.tool_output === undefined) return null;
  if (isFailedStatus(step.status) || step.is_error) {
    return formatStepValue(step.tool_output, 520);
  }
  if (step.tool_name === "load_skill") return summarizeLoaderOutput(step, step.tool_output);
  if (step.tool_name === "read_skill_file") {
    return summarizeSkillFileOutput(step, step.tool_output);
  }
  if (step.tool_output === "") return "";
  return formatStepValue(step.tool_output, 520);
}

function isSkillLoaderStep(step: TimelineStep) {
  if (isFailedStatus(step.status) || step.is_error) return false;
  return step.tool_name === "load_skill" || step.tool_name === "read_skill_file";
}

function ApprovalDecisionProgress({ step }: { step: TimelineStep }) {
  const waiting = step.status === "waiting_approval";
  const rejected = step.tool_status === "rejected" || isFailedStatus(step.status);

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <PhaseChip label="Chờ duyệt" active={waiting} done={!waiting} />
      <PhaseChip label={rejected ? "Từ chối" : "Duyệt"} active={false} done={!waiting} />
    </div>
  );
}

function ApprovalStepBody({
  step,
  input,
}: {
  step: TimelineStep;
  input: string | null;
}) {
  const waiting = step.status === "waiting_approval";
  const rejected = step.tool_status === "rejected" || isFailedStatus(step.status);
  const showPayload = Boolean(input) && (waiting || rejected);
  const message = waiting
    ? "Đang chờ người vận hành quyết định."
    : rejected
      ? "Người vận hành đã từ chối, tool không được thực thi."
      : "Người vận hành đã duyệt. Tool đã chuyển sang bước thực thi.";

  return (
    <div className="mt-3 grid gap-3">
      <ApprovalDecisionProgress step={step} />

      {showPayload ? (
        <section className="rounded-md border border-warm-border bg-code-block/55 px-3 py-2">
          <p className="mb-1 text-[10px] font-semibold uppercase text-secondary-text">
            Payload cần duyệt
          </p>
          <pre className="max-h-36 overflow-auto whitespace-pre-wrap font-mono text-[11px] leading-5 text-primary-text">
            {input}
          </pre>
        </section>
      ) : null}

      <p
        className={`rounded-md border px-3 py-2 text-xs font-medium ${
          waiting
            ? "border-accent-active/20 bg-accent-active/5 text-accent-active"
            : rejected
              ? "border-status-crimson/25 bg-status-crimson/5 text-status-crimson"
              : "border-status-sage/25 bg-status-sage/5 text-status-sage"
        }`}
      >
        {message}
      </p>
    </div>
  );
}

function currentThinkingStatus(steps: TimelineStep[], loading: boolean, streaming: boolean) {
  if (loading) return "Đang tải timeline của run";
  const runningStep = steps.find((step) => step.status === "running");
  const failedStep = steps.find((step) => isFailedStatus(step.status) || step.is_error);
  if (runningStep?.step_type === "llm_call") {
    const hasToolResult = steps.some(
      (step) => step.step_type === "tool_call" && isCompletedStatus(step.status),
    );
    return hasToolResult
      ? "AI đang tổng hợp kết quả từ các tool"
      : "AI đang đọc ngữ cảnh và chọn bước tiếp theo";
  }
  if (runningStep?.step_type === "tool_call") return `AI đang gọi ${runningStep.tool_name ?? runningStep.name}`;
  if (runningStep?.step_type === "approval") return "Đang chờ bạn xác nhận tác vụ";
  if (failedStep) return "Luồng xử lý gặp lỗi ở một node";
  if (streaming) return "AI đang tổng hợp câu trả lời";
  if (steps.length) return "Các node đã chạy xong, đang hiển thị kết quả";
  return "Timeline sẽ xuất hiện khi backend bắt đầu chạy";
}

function ResourceIcon({ kind }: { kind: ResourceItem["kind"] }) {
  if (kind === "ssh") return <TerminalIcon className="h-4 w-4" />;
  if (kind === "clickhouse") return <DatabaseIcon className="h-4 w-4" />;
  return <ServerIcon className="h-4 w-4" />;
}

function approvalToolName(approval: PendingApproval) {
  if (approval.tool_name) return approval.tool_name;
  return "tool";
}

function approvalInputRows(approval: PendingApproval) {
  const input = approval.tool_input ?? {};
  const rows: Array<[string, string]> = [];
  const fieldMap: Array<[string, string]> = [
    ["node_name", "Node"],
    ["command", "Command"],
    ["sql", "SQL"],
    ["skill_name", "Skill"],
    ["file_path", "File"],
  ];

  for (const [key, label] of fieldMap) {
    const value = input[key];
    if (typeof value === "string" && value.trim()) rows.push([label, value]);
  }

  if (!rows.length && Object.keys(input).length) {
    rows.push(["Payload", JSON.stringify(input, null, 2)]);
  }

  return rows;
}

function Sidebar({
  sessions,
  activeSessionId,
  onSelect,
  onDelete,
}: {
  sessions: ChatSession[];
  activeSessionId: string | null;
  onSelect: (sessionId: string) => void;
  onDelete: (sessionId: string) => void;
}) {
  const grouped = groupSessions(sessions);

  return (
    <aside className="hidden h-screen w-[260px] shrink-0 flex-col border-r border-warm-border bg-surface-card md:flex">
      <div className="flex h-14 items-center gap-2 border-b border-warm-border px-4">
        <TelecomLogo className="h-6 w-6 text-accent-active" />
        <span className="text-sm font-semibold tracking-wide text-primary-text">Telecom Agent</span>
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
                      <TrashIcon className="h-4 w-4" />
                    </button>
                  </div>
                ))}
              </div>
            </section>
          ) : null,
        )}
      </div>
      <nav className="grid grid-cols-2 gap-2 border-t border-warm-border p-3">
        <Link
          className="flex items-center justify-center gap-1.5 rounded-lg border border-warm-border bg-main-background/50 py-2.5 text-center font-mono text-[10px] font-semibold uppercase tracking-wider text-secondary-text transition-all duration-200 hover:border-accent-active/40 hover:bg-accent-active/5 hover:text-accent-active hover:shadow-sm"
          href="/admin/skills"
        >
          <SkillsIcon className="h-3.5 w-3.5" /> Skills
        </Link>
        <Link
          className="flex items-center justify-center gap-1.5 rounded-lg border border-warm-border bg-main-background/50 py-2.5 text-center font-mono text-[10px] font-semibold uppercase tracking-wider text-secondary-text transition-all duration-200 hover:border-accent-active/40 hover:bg-accent-active/5 hover:text-accent-active hover:shadow-sm"
          href="/admin/skills/upload"
        >
          <UploadIcon className="h-3.5 w-3.5" /> Upload
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
  onResolve: (action: "approved" | "rejected") => void;
}) {
  const toolName = approvalToolName(approval);
  const inputRows = approvalInputRows(approval);
  const approveLabel = "Cho chạy";

  return (
    <div
      className="rounded-lg border border-accent-active bg-[#fff8eb] p-4"
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-bold uppercase text-primary-text">
            Cần duyệt: {toolName}
          </h3>
          <p className="mt-1 text-sm text-secondary-text">
            Tác vụ có thay đổi trạng thái cần xác nhận trước khi chạy.
          </p>
        </div>
        <span
          className="shrink-0 rounded-md border border-accent-active/30 bg-accent-active/10 px-2 py-1 text-[11px] font-semibold text-accent-active"
        >
          Chờ duyệt
        </span>
      </div>

      {inputRows.length ? (
        <dl className="space-y-2">
          {inputRows.map(([label, value]) => (
            <div key={label} className="grid gap-1 text-sm sm:grid-cols-[96px_1fr] sm:items-start">
              <dt className="text-[11px] font-semibold uppercase text-secondary-text">{label}</dt>
              <dd className="min-w-0 break-words rounded-md bg-surface-card px-2.5 py-2 font-mono text-xs text-primary-text">
                {value}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="rounded-md bg-surface-card px-2.5 py-2 text-sm text-primary-text">
          Không có payload thực thi để hiển thị.
        </p>
      )}

      <div className="mt-3 grid grid-cols-2 gap-3">
        <button
          type="button"
          onClick={() => onResolve("rejected")}
          className="h-10 rounded-lg bg-code-block text-sm font-semibold text-primary-text transition hover:bg-warm-border"
        >
          Từ chối
        </button>
        <button
          type="button"
          onClick={() => onResolve("approved")}
          className="h-10 rounded-lg bg-accent-active text-sm font-semibold text-white transition hover:bg-[#b86205]"
        >
          {approveLabel}
        </button>
      </div>
    </div>
  );
}

function TimelinePanel({ steps }: { steps: TimelineStep[] }) {
  if (!steps.length) {
    return (
      <div className="rounded-lg border border-dashed border-warm-border p-4 text-sm text-secondary-text">
        Chưa có bước chạy. Khi run bắt đầu, các node thực thi sẽ xuất hiện ở đây.
      </div>
    );
  }

  return (
    <ol className="space-y-4">
      {steps.map((step, index) => {
        const input = formatStepValue(step.tool_input, 420);
        const output = formatToolOutput(step);
        const isTool = step.step_type === "tool_call";
        const isApproval = step.step_type === "approval";
        const displayStatus = isApproval && step.tool_status === "rejected" ? "rejected" : step.status;
        const outputIsSummary = isSkillLoaderStep(step);

        return (
          <li key={step.id} className="relative grid grid-cols-[32px_1fr] gap-3">
            <div className="relative flex justify-center pt-2">
              {index > 0 ? (
                <span className="absolute -top-4 h-6 w-px bg-warm-border" />
              ) : null}
              <span
                className={`z-10 flex h-8 w-8 items-center justify-center rounded-full border text-[10px] font-semibold shadow-sm ${stepTone(
                  step.status,
                )}`}
              >
                {step.status === "running" ? (
                  <span className="h-3 w-3 animate-spin rounded-full border border-accent-active border-t-transparent" />
                ) : (
                  stepIcon(step)
                )}
              </span>
              {index < steps.length - 1 ? (
                <span className="absolute top-10 h-[calc(100%+1rem)] w-px bg-warm-border" />
              ) : null}
            </div>

            <article
              className={`relative min-w-0 rounded-lg border bg-surface-card p-3 shadow-sm ${
                step.status === "running"
                  ? "border-accent-active ring-1 ring-accent-active/20"
                  : isFailedStatus(step.status) || step.is_error
                    ? "border-status-crimson/40"
                    : "border-warm-border"
              }`}
            >
              <span className="absolute -left-[7px] top-5 h-3 w-3 rounded-full border border-warm-border bg-surface-card" />
              <div className="flex min-w-0 items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-md bg-code-block px-2 py-0.5 text-[10px] font-semibold uppercase text-secondary-text">
                      {isApproval ? "Approval" : isTool ? "Tool" : stepTypeLabel(step)}
                    </span>
                    {outputIsSummary ? (
                      <span className="rounded-md border border-warm-border px-2 py-0.5 text-[10px] font-medium text-secondary-text">
                        Skill loader
                      </span>
                    ) : null}
                  </div>
                  <h3 className="mt-2 truncate text-sm font-semibold text-primary-text">
                    {isApproval ? getApprovalTitle(step) : getStepTitle(step)}
                  </h3>
                </div>
                <span className={`shrink-0 rounded-full border px-2 py-1 text-[11px] font-semibold ${statusBadgeClass(displayStatus)}`}>
                  {statusLabel(displayStatus)}
                </span>
              </div>

              {isApproval ? (
                <ApprovalStepBody step={step} input={input} />
              ) : isTool ? (
                <div className="mt-3 grid gap-2">
                  {input ? (
                    <section className="rounded-md bg-code-block/70 px-3 py-2">
                      <p className="mb-1 text-[10px] font-semibold uppercase text-secondary-text">
                        Input
                      </p>
                      <pre className="max-h-32 overflow-auto whitespace-pre-wrap font-mono text-[11px] leading-5 text-primary-text">
                        {input}
                      </pre>
                    </section>
                  ) : null}
                  {output !== null ? (
                    <section
                      className={`rounded-md px-3 py-2 ${
                        outputIsSummary
                          ? "bg-code-block/70 text-primary-text"
                          : "bg-terminal-bg text-terminal-fg"
                      }`}
                    >
                      <p
                        className={`mb-1 text-[10px] font-semibold uppercase ${
                          outputIsSummary ? "text-secondary-text" : "text-terminal-fg/65"
                        }`}
                      >
                        Output{step.output_truncated && !outputIsSummary ? " · đã rút gọn" : ""}
                      </p>
                      <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px] leading-5">
                        {output}
                      </pre>
                    </section>
                  ) : (
                    <p className="rounded-md bg-code-block/60 px-3 py-2 text-xs text-secondary-text">
                      Chưa có output.
                    </p>
                  )}
                </div>
              ) : (
                <div className="mt-3 max-h-48 overflow-y-auto pr-1 text-xs leading-6 text-secondary-text/90 border-l border-warm-border/50 pl-2 message-markdown">
                  <MarkdownMessage content={getStepSummary(step)} />
                </div>
              )}
            </article>
          </li>
        );
      })}
    </ol>
  );
}

function PhaseChip({
  label,
  active,
  done,
}: {
  label: string;
  active: boolean;
  done: boolean;
}) {
  return (
    <span
      className={`inline-flex min-w-0 items-center justify-center rounded-md border px-2 py-1 text-[10px] font-semibold uppercase ${
        active
          ? "border-accent-active/40 bg-accent-active/10 text-accent-active"
          : done
            ? "border-status-sage/25 bg-status-sage/10 text-status-sage"
            : "border-warm-border bg-surface-card text-secondary-text"
      }`}
    >
      {label}
    </span>
  );
}

function ThinkingPanel({
  steps,
  open,
  loading,
  streaming,
  onToggle,
}: {
  steps: TimelineStep[];
  open: boolean;
  loading: boolean;
  streaming: boolean;
  onToggle: () => void;
}) {
  const completed = steps.filter((step) => isCompletedStatus(step.status)).length;
  const failed = steps.filter((step) => isFailedStatus(step.status) || step.is_error).length;
  const runningStep = steps.find((step) => step.status === "running");
  const statusText = failed
    ? `${failed} lỗi`
    : steps.length
      ? `${completed}/${steps.length} node xong`
      : loading
        ? "Đang tải"
        : "Chưa có node";
  const progress = steps.length ? Math.round((completed / steps.length) * 100) : 0;
  const thinkingStatus = currentThinkingStatus(steps, loading, streaming);
  const hasReason = steps.some((step) => step.step_type === "llm_call");
  const hasTool = steps.some((step) => step.step_type === "tool_call");
  const hasApproval = steps.some((step) => step.step_type === "approval");

  return (
    <section className="rounded-lg border border-warm-border/80 bg-code-block/45">
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          onToggle();
        }}
        className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left"
        aria-expanded={open}
      >
        <span className="flex min-w-0 items-center gap-2">
          <span
            className={`h-2 w-2 shrink-0 rounded-full ${
              failed
                ? "bg-status-crimson"
                : streaming || runningStep || loading
                  ? "animate-pulse bg-accent-active"
                  : "bg-status-sage"
            }`}
          />
          <span className="truncate text-xs font-semibold text-primary-text">AI đang xử lý</span>
          <span className="truncate text-[11px] text-secondary-text">{statusText}</span>
        </span>
        <span className="shrink-0 font-mono text-xs text-secondary-text">{open ? "−" : "+"}</span>
      </button>
      {open ? (
        <div className="border-t border-warm-border px-3 pb-3 pt-2.5">
          <p className="text-xs leading-5 text-primary-text">{thinkingStatus}</p>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-surface-card">
            <span
              className={`block h-full rounded-full ${
                failed ? "bg-status-crimson" : "bg-accent-active"
              }`}
              style={{ width: steps.length ? `${Math.max(progress, runningStep ? 8 : 0)}%` : "0%" }}
            />
          </div>
          <div className="mt-3 grid grid-cols-3 gap-1.5">
            <PhaseChip
              label="Reason"
              active={runningStep?.step_type === "llm_call"}
              done={hasReason && !steps.some((step) => step.step_type === "llm_call" && step.status === "running")}
            />
            <PhaseChip
              label="Tool"
              active={runningStep?.step_type === "tool_call"}
              done={hasTool && !steps.some((step) => step.step_type === "tool_call" && step.status === "running")}
            />
            <PhaseChip
              label="Duyệt"
              active={runningStep?.step_type === "approval"}
              done={hasApproval && !steps.some((step) => step.step_type === "approval" && step.status === "running")}
            />
          </div>
        </div>
      ) : null}
    </section>
  );
}

function AssistantMessageBubble({
  message,
  isSelected,
  steps,
  timelineLoading,
  thinkingOpen,
  onToggleThinking,
  onSelectTimeline,
}: {
  message: ChatMessage;
  isSelected: boolean;
  steps: TimelineStep[];
  timelineLoading: boolean;
  thinkingOpen: boolean;
  onToggleThinking: () => void;
  onSelectTimeline: () => void;
}) {
  const hasTimeline = Boolean(message.run_id);
  const showThinking = hasTimeline && message.status === "streaming";
  const handleTimelineKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!hasTimeline || (event.key !== "Enter" && event.key !== " ")) return;
    event.preventDefault();
    onSelectTimeline();
  };

  return (
    <div
      onClick={hasTimeline ? onSelectTimeline : undefined}
      onKeyDown={hasTimeline ? handleTimelineKeyDown : undefined}
      role={hasTimeline ? "button" : undefined}
      tabIndex={hasTimeline ? 0 : undefined}
      aria-pressed={hasTimeline ? isSelected : undefined}
      aria-label={
        hasTimeline
          ? isSelected
            ? "Đang xem chi tiết bước của phản hồi"
            : "Xem chi tiết bước của phản hồi"
          : undefined
      }
      className={`flex w-full max-w-[min(760px,92%)] flex-col gap-2 rounded-lg border px-4 py-3 transition ${
        hasTimeline ? "cursor-pointer" : ""
      } ${
        message.status === "error"
          ? "border-status-crimson/40 bg-status-crimson/5 hover:border-status-crimson/60"
          : isSelected
            ? "border-accent-active bg-surface-card ring-1 ring-accent-active/30"
            : "border-warm-border bg-surface-card hover:border-accent-active/50"
      }`}
    >
      {showThinking ? (
        <ThinkingPanel
          steps={steps}
          open={thinkingOpen || message.status === "streaming"}
          loading={timelineLoading}
          streaming={message.status === "streaming"}
          onToggle={onToggleThinking}
        />
      ) : null}

      <div className="min-w-0">
        <div className="mb-2 flex items-center justify-between gap-3">
          <span className="text-[11px] font-semibold uppercase text-secondary-text">
            Kết luận
          </span>
          {hasTimeline ? (
            <span
              className={`text-[9px] font-mono uppercase tracking-wider transition ${
                isSelected
                  ? "text-accent-active font-semibold"
                  : "text-secondary-text/60"
              }`}
            >
              {isSelected ? "● Đang xem bước" : "○ Click để xem chi tiết bước"}
            </span>
          ) : null}
        </div>
        <MarkdownMessage content={message.content} />
      </div>
    </div>
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
          Bước chạy
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
  const [expandedThinking, setExpandedThinking] = useState<Record<string, boolean>>({});
  const [resources, setResources] = useState<ResourceItem[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"timeline" | "resources">("timeline");
  const activateTimeline = useCallback(() => setActiveTab("timeline"), []);
  const {
    steps,
    selectedTimelineRunId,
    timelineLoading,
    clearTimeline,
    selectTimelineRunId,
    getSelectedTimelineRunId,
    applyTimelineUpdate,
    loadRunTimeline,
  } = useRunTimeline({ onActivate: activateTimeline, onError: setError });
  const [approval, setApproval] = useState<PendingApproval | null>(null);
  const [chatOptions, setChatOptions] = useState<ChatOptions>({
    default_model: null,
    models: [],
    skills: [],
    capabilities: [],
  });
  const [selectedModel, setSelectedModel] = useState<
    Pick<ChatModelOption, "provider" | "model"> | null
  >(null);
  const [selectedSkillName, setSelectedSkillName] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const isMountedRef = useRef(true);
  const activeRunIdRef = useRef<string | null>(null);
  const activeAssistantIdRef = useRef<string | null>(null);
  const streamGenerationRef = useRef(0);
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

  const skillQuery = useMemo(() => {
    if (streaming) return null;
    const match = prompt.match(/(^|\s)\/([\w.-]*)$/);
    return match ? match[2] : null;
  }, [prompt, streaming]);

  const selectedSkill = chatOptions.skills.find((skill) => skill.name === selectedSkillName);

  const loadSessionMessages = useCallback(async (sessionId: string) => {
    const requestId = historyRequestRef.current + 1;
    historyRequestRef.current = requestId;
    setActiveSessionId(sessionId);
    setError(null);
    setApproval(null);
    clearTimeline();
    setExpandedThinking({});
    setStreaming(false);
    setActiveRunId(null);
    activeRunIdRef.current = null;
    activeAssistantIdRef.current = null;
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

      const restoredMessages = history
        .filter(
          (
            message,
          ): message is PersistedChatMessage & { role: "user" | "assistant" } =>
            message.role === "user" || message.role === "assistant",
        )
        .map(chatMessageFromPersisted);
      let nextMessages = restoredMessages;

      const missingRunId = findRunMissingAssistant(restoredMessages);
      if (missingRunId) {
        try {
          const timeline = await apiFetch<RunTimelineResponse>(`/runs/${missingRunId}/timeline`);
          if (historyRequestRef.current !== requestId) return;

          const recoveredAssistant = recoveredAssistantFromTimeline(missingRunId, timeline);
          nextMessages = [...restoredMessages, recoveredAssistant];
          selectTimelineRunId(missingRunId);
          applyTimelineUpdate(missingRunId, timeline.steps);
          setActiveTab("timeline");
          setExpandedThinking((current) => ({
            ...current,
            [missingRunId]: recoveredAssistant.status === "streaming",
          }));

          if (recoveredAssistant.status === "streaming") {
            setStreaming(true);
            setActiveRunId(missingRunId);
            activeRunIdRef.current = missingRunId;
            activeAssistantIdRef.current = recoveredAssistant.id;
          }

          if (timeline.status === "waiting_approval") {
            try {
              const pendingApprovals = await apiFetch<PendingApprovalDetail[]>("/approvals/pending");
              if (historyRequestRef.current !== requestId) return;
              const pendingApproval = pendingApprovals.find(
                (item) => item.run_id === missingRunId && item.status === "pending",
              );
              if (pendingApproval) setApproval(approvalFromPendingDetail(pendingApproval));
            } catch {
              // Timeline recovery still works even if the approval list is temporarily unavailable.
            }
          }
        } catch {
          if (historyRequestRef.current !== requestId) return;
          nextMessages = [
            ...restoredMessages,
            {
              id: `recovered-${missingRunId}`,
              role: "assistant",
              content: "Không khôi phục được trạng thái run sau khi tải lại trang.",
              status: "error",
              run_id: missingRunId,
            },
          ];
        }
      }

      setMessages(nextMessages);
    } catch (exc) {
      if (historyRequestRef.current !== requestId) return;
      setMessages([]);
      setError(exc instanceof Error ? exc.message : "Không tải được lịch sử phiên chat.");
    } finally {
      if (historyRequestRef.current === requestId) setLoadingMessages(false);
    }
  }, [applyTimelineUpdate, clearTimeline, selectTimelineRunId]);

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
    apiFetch<ChatOptions>("/chat/options")
      .then((options) => {
        setChatOptions(options);
        setSelectedModel(
          (current) =>
            current ?? options.default_model ?? options.models.find((model) => model.available) ?? null,
        );
      })
      .catch(() => setChatOptions({ default_model: null, models: [], skills: [], capabilities: [] }));
  }, [loadSessionMessages]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, approval]);

  useEffect(() => {
    if (!activeRunId || abortRef.current) return;

    let cancelled = false;
    const pollingRunId = activeRunId;
    const recoveredAssistantId = activeAssistantIdRef.current;

    async function pollRecoveredRun() {
      try {
        const timeline = await apiFetch<RunTimelineResponse>(`/runs/${pollingRunId}/timeline`);
        if (cancelled || activeRunIdRef.current !== pollingRunId) return;

        applyTimelineUpdate(pollingRunId, timeline.steps);

        if (timeline.status === "waiting_approval") {
          try {
            const pendingApprovals = await apiFetch<PendingApprovalDetail[]>("/approvals/pending");
            if (cancelled || activeRunIdRef.current !== pollingRunId) return;
            const pendingApproval = pendingApprovals.find(
              (item) => item.run_id === pollingRunId && item.status === "pending",
            );
            if (pendingApproval) setApproval(approvalFromPendingDetail(pendingApproval));
          } catch {
            // Keep polling the timeline even if approval metadata is temporarily unavailable.
          }
        }

        if (!ACTIVE_RUN_STATUSES.has(timeline.status) && !timelineHasOpenStep(timeline.steps)) {
          const recoveredAssistant = recoveredAssistantFromTimeline(pollingRunId, timeline);
          if (recoveredAssistantId) {
            setMessages((current) =>
              current.map((message) =>
                message.id === recoveredAssistantId ? recoveredAssistant : message,
              ),
            );
          }
          if (recoveredAssistant.status !== "streaming") {
            setStreaming(false);
            setActiveRunId(null);
            activeRunIdRef.current = null;
            activeAssistantIdRef.current = null;
            setApproval(null);
            setExpandedThinking((current) => ({ ...current, [pollingRunId]: false }));
            apiFetch<ChatSession[]>("/sessions").then(setSessions).catch(() => undefined);
          }
        }
      } catch {
        // A transient poll failure should not erase the recovered conversation state.
      }
    }

    void pollRecoveredRun();
    const intervalId = window.setInterval(() => void pollRecoveredRun(), 3000);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [activeRunId, applyTimelineUpdate]);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      abortRef.current?.abort();
    };
  }, []);

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
    clearTimeline();
    setExpandedThinking({});
    setActiveRunId(null);
    activeRunIdRef.current = null;
    activeAssistantIdRef.current = null;
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
      clearTimeline();
      setExpandedThinking({});
      setActiveRunId(null);
      activeRunIdRef.current = null;
      activeAssistantIdRef.current = null;
    }
    if (!sessionId.startsWith("session-")) {
      await apiFetch(`/sessions/${sessionId}`, { method: "DELETE" }).catch((exc: Error) =>
        setError(exc.message),
      );
    }
  }

  function handleSseEvent(
    event: ParsedSseEvent,
    assistantId: string,
    streamGeneration?: number,
  ) {
    if (streamGeneration !== undefined && streamGenerationRef.current !== streamGeneration) {
      return;
    }
    const payload = event.data;
    if (event.event === "run_started") {
      const runId = typeof payload.run_id === "string" ? payload.run_id : null;
      if (runId) {
        selectTimelineRunId(runId);
        setActiveRunId(runId);
        activeRunIdRef.current = runId;
        setExpandedThinking((current) => ({ ...current, [runId]: true }));
        updateAssistant(assistantId, (message) => ({ ...message, run_id: runId }));
      }
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
      const runId =
        typeof payload.run_id === "string" ? payload.run_id : getSelectedTimelineRunId();
      if (runId) applyTimelineUpdate(runId, payload.steps as TimelineStep[]);
    }
    if (event.event === "run_suspended") {
      setMessages((current) => current.filter((message) => message.id !== assistantId));
      if (activeAssistantIdRef.current === assistantId) {
        activeAssistantIdRef.current = null;
      }
      setStreaming(false);
      setApproval({
        approval_request_id: String(payload.approval_request_id ?? ""),
        run_id: typeof payload.run_id === "string" ? payload.run_id : undefined,
        tool_name: typeof payload.tool_name === "string" ? payload.tool_name : null,
        tool_input:
          payload.tool_input && typeof payload.tool_input === "object" && !Array.isArray(payload.tool_input)
            ? (payload.tool_input as Record<string, unknown>)
            : null,
        risk_level: typeof payload.risk_level === "string" ? payload.risk_level : null,
        status: "pending",
      });
    }
    if (event.event === "run_completed") {
      const finalAnswer = typeof payload.final_answer === "string" ? payload.final_answer : "";
      updateAssistant(assistantId, (message) => ({
        ...message,
        content: finalAnswer || message.content,
        status: "done",
        run_id: typeof payload.run_id === "string" ? payload.run_id : message.run_id,
      }));
      if (typeof payload.run_id === "string") {
        setExpandedThinking((current) => ({ ...current, [payload.run_id as string]: false }));
        if (activeRunIdRef.current === payload.run_id) {
          activeRunIdRef.current = null;
          setActiveRunId(null);
        }
      }
      setStreaming(false);
      setApproval(null);
      apiFetch<ChatSession[]>("/sessions").then(setSessions).catch(() => undefined);
    }
    if (event.event === "run_failed" || event.event === "error") {
      const message = String(payload.error ?? payload.message ?? "Run failed.");
      updateAssistant(assistantId, (current) => ({
        ...current,
        content: current.content ? `${current.content}\n\n${message}` : message,
        status: "error",
      }));
      activeRunIdRef.current = null;
      setActiveRunId(null);
      setStreaming(false);
      setApproval(null);
    }
  }

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = prompt.trim();
    if (!content) return;

    setError(null);
    setApproval(null);

    if (streaming) {
      const runId = activeRunIdRef.current;
      if (!runId) {
        setError("Run chưa sẵn sàng nhận can thiệp. Đợi sự kiện run_started rồi thử lại.");
        return;
      }
      try {
        const queued = await apiFetch<PersistedChatMessage>(`/runs/${runId}/interventions`, {
          method: "POST",
          body: JSON.stringify({ content, requested_by: "operator_admin" }),
        });
        setMessages((current) => {
          const activeAssistantId = activeAssistantIdRef.current;
          const activeAssistant = activeAssistantId
            ? current.find((message) => message.id === activeAssistantId)
            : undefined;
          const withoutActiveAssistant = activeAssistantId
            ? current.filter((message) => message.id !== activeAssistantId)
            : current;
          return [
            ...withoutActiveAssistant,
            {
              id: queued.id,
              role: "user",
              content: queued.content,
              status: queued.status === "failed" ? "error" : "done",
              run_id: queued.run_id,
            },
            ...(activeAssistant ? [activeAssistant] : []),
          ];
        });
        setPrompt("");
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : "Không queue được can thiệp vào run.");
      }
      return;
    }

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
    activeRunIdRef.current = null;
    setActiveRunId(null);
    activeAssistantIdRef.current = assistantMessage.id;
    const streamGeneration = streamGenerationRef.current + 1;
    streamGenerationRef.current = streamGeneration;

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetch(apiUrl("/chat/stream"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          user_message: content,
          provider: selectedModel?.provider,
          model: selectedModel?.model,
          skill_mode: selectedSkillName ? "specific" : "auto",
          ...(selectedSkillName ? { skill_name: selectedSkillName } : {}),
        }),
        signal: controller.signal,
      });
      await consumeSseStream(response, (sseEvent) =>
        handleSseEvent(sseEvent, assistantMessage.id, streamGeneration),
      );
    } catch (exc) {
      if (controller.signal.aborted) {
        if (isMountedRef.current) {
          updateAssistant(assistantMessage.id, (message) => ({
            ...message,
            content: message.content || "Đã dừng stream theo yêu cầu người trực.",
            status: "done",
          }));
        }
      } else if (isMountedRef.current && streamGenerationRef.current === streamGeneration) {
        const message = exc instanceof Error ? exc.message : "Không mở được SSE stream.";
        setError(message);
        updateAssistant(assistantMessage.id, (current) => ({
          ...current,
          content: message,
          status: "error",
        }));
      }
    } finally {
      if (streamGenerationRef.current === streamGeneration) {
        if (isMountedRef.current) setStreaming(false);
        if (abortRef.current === controller) abortRef.current = null;
        if (activeAssistantIdRef.current === assistantMessage.id) activeAssistantIdRef.current = null;
      }
    }
  }

  async function resolveApproval(action: "approved" | "rejected") {
    if (!approval) return;
    const currentApproval = approval;
    setApproval(null);
    const assistantMessage: ChatMessage = {
      id: id("assistant"),
      role: "assistant",
      content: "",
      status: "streaming",
      run_id: currentApproval.run_id,
    };
    setMessages((current) => [...current, assistantMessage]);
    activeAssistantIdRef.current = assistantMessage.id;

    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(true);

    try {
      const response = await fetch(apiUrl(`/approvals/${currentApproval.approval_request_id}/resolve`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
        signal: controller.signal,
      });
      await consumeSseStream(response, (sseEvent) => handleSseEvent(sseEvent, assistantMessage.id));
    } catch (exc) {
      if (controller.signal.aborted) {
        if (isMountedRef.current) {
          updateAssistant(assistantMessage.id, (current) => ({
            ...current,
            content: current.content || "Đã dừng stream theo yêu cầu người trực.",
            status: "done",
          }));
        }
      } else if (isMountedRef.current) {
        const message = exc instanceof Error ? exc.message : "Không resolve được approval.";
        setError(message);
        updateAssistant(assistantMessage.id, (current) => ({
          ...current,
          content: current.content || message,
          status: "error",
        }));
        setApproval(currentApproval);
      }
    } finally {
      if (isMountedRef.current) setStreaming(false);
      if (abortRef.current === controller) abortRef.current = null;
      if (activeAssistantIdRef.current === assistantMessage.id) activeAssistantIdRef.current = null;
    }
  }

  function pickResource(resource: ResourceItem) {
    setPrompt((current) => current.replace(/(^|\s)@([\w.-]*)$/, `$1@${resource.name} `));
  }

  function pickSkill(skill: ChatSkillOption) {
    setSelectedSkillName(skill.name);
    setPrompt((current) => current.replace(/(^|\s)\/[\w.-]*$/, "$1").trimEnd());
  }

  async function stopActiveRun() {
    const runId = activeRunIdRef.current;
    const assistantId = activeAssistantIdRef.current;
    if (runId) {
      await apiFetch(`/runs/${runId}/cancel`, {
        method: "POST",
        body: JSON.stringify({
          reason: "Run stopped by operator from chat UI.",
          requested_by: "operator_admin",
        }),
      }).catch((exc: Error) => setError(exc.message));
    }
    abortRef.current?.abort();
    if (assistantId) {
      updateAssistant(assistantId, (message) => ({
        ...message,
        content: message.content || "Đã dừng stream theo yêu cầu người trực.",
        status: "done",
      }));
    }
    activeRunIdRef.current = null;
    setActiveRunId(null);
  }

  return (
    <main className="flex h-screen overflow-hidden bg-main-background text-primary-text">
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelect={(sessionId) => void loadSessionMessages(sessionId)}
        onDelete={(sessionId) => void deleteSession(sessionId)}
      />
      <section className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-warm-border bg-main-background px-5">
          <div className="flex items-center gap-3">
            <TelecomLogo className="h-6 w-6 text-accent-active md:hidden" />
            <div className="flex items-center gap-2 text-xs">
              <span
                className={`h-2 w-2 rounded-full ${
                  activeRunId ? "animate-pulse bg-accent-active" : "bg-status-sage"
                }`}
              />
              <span className="font-medium text-secondary-text">
                {activeRunId ? "Agent đang chạy · có thể can thiệp" : "Backend stream ready"}
              </span>
            </div>
          </div>
          <button
            type="button"
            onClick={() => void createSession()}
            aria-label="Phiên hội thoại mới"
            className="flex h-9 w-11 items-center justify-center gap-2 rounded-lg border border-warm-border bg-surface-card text-xs font-medium transition hover:border-accent-active hover:bg-main-background sm:w-auto sm:px-4"
          >
            <PlusIcon className="h-4 w-4" />
            <span className="hidden sm:inline">Phiên hội thoại mới</span>
          </button>
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

            {messages.map((message) => {
              const isSelected =
                Boolean(message.run_id) && selectedTimelineRunId === message.run_id;
              const runSteps = isSelected ? steps : [];

              return (
                <article
                  key={message.id}
                  className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
                >
                  {message.role === "user" ? (
                    <div className="max-w-[min(680px,82%)] rounded-lg border border-accent-active/30 bg-accent-active px-4 py-3 text-white">
                      <MarkdownMessage content={message.content} />
                    </div>
                  ) : (
                    <AssistantMessageBubble
                      message={message}
                      isSelected={isSelected}
                      steps={runSteps}
                      timelineLoading={timelineLoading}
                      thinkingOpen={
                        message.run_id ? Boolean(expandedThinking[message.run_id]) : false
                      }
                      onToggleThinking={() => {
                        if (!message.run_id) return;
                        setExpandedThinking((current) => ({
                          ...current,
                          [message.run_id as string]: !current[message.run_id as string],
                        }));
                        if (!isSelected) void loadRunTimeline(message.run_id);
                      }}
                      onSelectTimeline={() => {
                        if (!message.run_id) return;
                        if (isSelected) {
                          clearTimeline();
                        } else {
                          void loadRunTimeline(message.run_id);
                        }
                      }}
                    />
                  )}
                </article>
              );
            })}

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
            <SkillCommandMenu
              skills={chatOptions.skills}
              query={skillQuery}
              selectedSkillName={selectedSkillName}
              onSelect={pickSkill}
            />
            <div className="rounded-xl border border-warm-border bg-surface-card p-2 focus-within:border-accent-active">
              {selectedSkill ? (
                <div className="px-2 pt-1">
                  <span className="inline-flex max-w-full items-center gap-1 rounded-md bg-main-background px-2 py-1 text-xs text-primary-text">
                    <span className="truncate">/{selectedSkill.name}</span>
                    <button
                      type="button"
                      onClick={() => setSelectedSkillName(null)}
                      aria-label={`Bỏ chọn skill ${selectedSkill.name}`}
                      className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-secondary-text transition hover:bg-code-block hover:text-primary-text"
                    >
                      <CloseIcon className="h-3.5 w-3.5" />
                    </button>
                  </span>
                </div>
              ) : null}
              <textarea
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                rows={2}
                placeholder={
                  streaming
                    ? "Agent đang chạy. Gõ chỉ đạo mới để can thiệp, hoặc để trống rồi bấm dừng..."
                    : "Hỏi agent, ví dụ: kiểm tra lag trên @hanoi-core-01..."
                }
                className="max-h-36 min-h-14 w-full resize-none border-0 bg-transparent px-2 py-3 text-sm outline-none placeholder:text-secondary-text"
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
              />
              <div className="flex min-w-0 items-center justify-between gap-2 border-t border-warm-border pt-2">
                <ModelPicker
                  models={chatOptions.models}
                  selectedModel={selectedModel}
                  disabled={streaming}
                  onSelectModel={setSelectedModel}
                />
                <button
                  type={streaming && !prompt.trim() ? "button" : "submit"}
                  onClick={streaming && !prompt.trim() ? () => void stopActiveRun() : undefined}
                  className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-white transition ${
                    streaming && !prompt.trim()
                      ? "bg-primary-text hover:bg-primary-text/85"
                      : "bg-accent-active hover:bg-[#b86205]"
                  }`}
                  aria-label={
                    streaming && prompt.trim()
                      ? "Gửi chỉ đạo can thiệp"
                      : streaming
                        ? "Dừng stream"
                        : "Gửi câu lệnh"
                  }
                >
                  {streaming && !prompt.trim() ? (
                    <StopIcon className="h-4 w-4" />
                  ) : streaming ? (
                    <InterveneIcon className="h-[18px] w-[18px]" />
                  ) : (
                    <SendIcon className="h-[18px] w-[18px]" />
                  )}
                </button>
              </div>
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
