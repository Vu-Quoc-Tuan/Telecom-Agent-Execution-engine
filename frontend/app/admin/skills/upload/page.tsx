"use client";

import Link from "next/link";
import { FormEvent, useMemo, useState } from "react";
import { apiUrl } from "@/lib/api";

type UploadResult = {
  status: string;
  message: string;
  skill_id?: string;
  pipeline_audit_logs?: string[];
};

const SAMPLE_SKILL = `---
name: inspect-olt-signal
description: Kiểm tra suy hao optical power và trạng thái uplink trên trạm OLT.
metadata:
  version: 1.0.0
allowed-tools:
  - ssh
---

# Inspect OLT Signal

Skill này chỉ đọc log/trạng thái tín hiệu phục vụ xử lý sự cố viễn thông.`;

function normalizeErrorPayload(data: unknown): UploadResult {
  if (typeof data === "object" && data !== null && "detail" in data) {
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === "object" && detail !== null) {
      const typed = detail as { status?: string; message?: string; skill_id?: string; logs?: string[] };
      return {
        status: typed.status ?? "REJECTED",
        message: typed.message ?? "Upload rejected.",
        skill_id: typed.skill_id,
        pipeline_audit_logs: typed.logs ?? [],
      };
    }
    return { status: "FAILED", message: String(detail) };
  }
  return { status: "FAILED", message: "Upload failed." };
}

export default function SkillUploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [skillName, setSkillName] = useState("inspect-olt-signal");
  const [description, setDescription] = useState("Đọc trạng thái tín hiệu OLT và trả về chẩn đoán.");
  const [code, setCode] = useState(SAMPLE_SKILL);
  const [sampleInput, setSampleInput] = useState('{"station":"hanoi-core-01","pon":"1/1/1"}');
  const [result, setResult] = useState<UploadResult | null>(null);
  const [running, setRunning] = useState(false);

  const consoleLines = useMemo(() => {
    if (running) {
      return [
        "[RUNNING] Khởi tạo upload stream tới backend...",
        "[RUNNING] Đóng gói zip skill và chờ sandbox phản hồi.",
      ];
    }
    if (result?.pipeline_audit_logs?.length) return result.pipeline_audit_logs;
    return [
      "[IDLE] Pipeline đang chờ gói skill .zip.",
      "[INFO] Backend hiện nhận Agent Skill package dạng zip chứa đúng một SKILL.md.",
      "[INFO] Mẫu bên trái dùng để soạn nội dung trước khi đóng gói.",
    ];
  }, [result, running]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setRunning(true);
    setResult(null);

    if (!file) {
      setRunning(false);
      setResult({ status: "FAILED", message: "Chọn file .zip trước khi kích nổ pipeline." });
      return;
    }

    const body = new FormData();
    body.append("file", file);

    try {
      const response = await fetch(apiUrl("/skills/upload"), { method: "POST", body });
      const data = await response.json();
      if (!response.ok) {
        setResult(normalizeErrorPayload(data));
        return;
      }
      setResult(data as UploadResult);
    } catch (exc) {
      setResult({
        status: "FAILED",
        message: exc instanceof Error ? exc.message : "Không gọi được backend upload.",
      });
    } finally {
      setRunning(false);
    }
  }

  return (
    <main className="flex min-h-screen flex-col bg-main-background text-primary-text">
      <header className="flex h-14 items-center justify-between border-b border-warm-border px-5">
        <div>
          <h1 className="text-sm font-semibold">Upload & thẩm định Skill</h1>
          <p className="text-xs text-secondary-text">AST scan · domain judge · pending review</p>
        </div>
        <nav className="flex items-center gap-2 text-sm">
          <Link className="rounded-md px-3 py-2 hover:bg-surface-card" href="/chat">
            Chat
          </Link>
          <Link className="rounded-md px-3 py-2 hover:bg-surface-card" href="/admin/skills">
            Skill registry
          </Link>
        </nav>
      </header>

      <form onSubmit={submit} className="grid min-h-0 flex-1 gap-6 p-6 lg:grid-cols-2">
        <section className="min-h-0 space-y-4 overflow-y-auto">
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="text-xs font-semibold uppercase text-secondary-text">Tên hàm/skill</span>
              <input
                value={skillName}
                onChange={(event) => setSkillName(event.target.value)}
                className="mt-2 h-10 w-full rounded-lg border border-warm-border bg-surface-card px-3 text-sm"
              />
            </label>
            <label className="block">
              <span className="text-xs font-semibold uppercase text-secondary-text">Gói zip</span>
              <input
                type="file"
                accept=".zip"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                className="mt-2 block h-10 w-full rounded-lg border border-warm-border bg-surface-card px-3 py-2 text-sm"
              />
            </label>
          </div>

          <label className="block">
            <span className="text-xs font-semibold uppercase text-secondary-text">Mô tả nghiệp vụ</span>
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              className="mt-2 min-h-20 w-full resize-none rounded-lg border border-warm-border bg-surface-card p-3 text-sm"
            />
          </label>

          <label className="block">
            <span className="text-xs font-semibold uppercase text-secondary-text">SKILL.md / mã nguồn preview</span>
            <textarea
              value={code}
              onChange={(event) => setCode(event.target.value)}
              spellCheck={false}
              className="mt-2 h-[360px] w-full resize-none rounded-lg border border-warm-border bg-surface-card p-4 font-mono text-sm leading-6"
            />
          </label>

          <label className="block">
            <span className="text-xs font-semibold uppercase text-secondary-text">Sample Input JSON</span>
            <textarea
              value={sampleInput}
              onChange={(event) => setSampleInput(event.target.value)}
              spellCheck={false}
              className="mt-2 min-h-24 w-full resize-none rounded-lg border border-warm-border bg-surface-card p-3 font-mono text-sm"
            />
          </label>
        </section>

        <section className="flex min-h-0 flex-col rounded-lg border border-[#36332d] bg-terminal-bg text-terminal-fg">
          <div className="flex h-11 items-center justify-between border-b border-white/10 px-4">
            <div className="flex gap-2">
              <span className="h-3 w-3 rounded-full bg-status-crimson" />
              <span className="h-3 w-3 rounded-full bg-accent-active" />
              <span className="h-3 w-3 rounded-full bg-status-sage" />
            </div>
            <span className="font-mono text-xs text-terminal-fg/70">validation-monitor</span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-4 font-mono text-sm leading-7">
            {consoleLines.map((line, index) => (
              <p
                key={`${line}-${index}`}
                className={line.includes("PASS") || line.includes("Passed") ? "text-status-sage" : ""}
              >
                <span className="text-terminal-fg/50">$</span> {line}
              </p>
            ))}
            {result ? (
              <div className="mt-5 rounded-lg border border-accent-active/50 bg-[#fff4d7] p-4 font-sans text-primary-text">
                <p className="text-xs font-bold uppercase text-accent-active">STATUS: {result.status}</p>
                <p className="mt-2 text-sm">{result.message}</p>
                {result.skill_id ? (
                  <p className="mt-2 font-mono text-xs text-secondary-text">skill_id={result.skill_id}</p>
                ) : null}
              </div>
            ) : null}
          </div>
          <div className="border-t border-white/10 p-4">
            <button
              disabled={running}
              className="h-11 w-full rounded-lg bg-accent-active text-sm font-semibold text-white transition hover:bg-[#b86205] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {running ? "Đang thẩm định..." : "Kích nổ Thẩm định Pipeline"}
            </button>
          </div>
        </section>
      </form>
    </main>
  );
}
