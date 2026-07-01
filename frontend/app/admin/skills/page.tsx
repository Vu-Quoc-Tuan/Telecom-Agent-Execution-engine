"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "@/lib/api";
import type { SkillStatus, SkillSummary } from "@/lib/types";
import { SkillReviewPanel } from "@/components/skill-review-panel";
import { ArrowLeftIcon, RefreshIcon, SkillsIcon, UploadIcon, TelecomLogo } from "@/components/icons";

const TABS: { id: SkillStatus; label: string }[] = [
  { id: "testing", label: "Chờ duyệt" },
  { id: "ready", label: "Kích hoạt" },
  { id: "rejected", label: "Cách ly" },
];


function formatErrorRate(errorRate: number | undefined) {
  return `${Math.round((errorRate ?? 0) * 100)}%`;
}

export default function SkillRegistryPage() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [activeTab, setActiveTab] = useState<SkillStatus>("testing");
  const [selected, setSelected] = useState<SkillSummary | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const visibleSkills = useMemo(
    () => skills.filter((skill) => skill.status === activeTab),
    [activeTab, skills],
  );

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      setSkills(await apiFetch<SkillSummary[]>("/skills"));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Không tải được registry.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    apiFetch<SkillSummary[]>("/skills")
      .then((data) => {
        if (!cancelled) setSkills(data);
      })
      .catch((exc: Error) => {
        if (!cancelled) setError(exc.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function review(skill: SkillSummary, action: "approve" | "reject") {
    setError(null);
    try {
      await apiFetch(`/skills/${skill.id}/${action}`, {
        method: "POST",
        body: JSON.stringify({ note }),
      });
      setSelected(null);
      setNote("");
      await refresh();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Review action failed.");
    }
  }

  return (
    <main className="flex h-screen w-screen flex-col bg-main-background text-primary-text overflow-hidden">
      {/* Top Header */}
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-warm-border bg-surface-card px-5 z-10">
        <div className="flex items-center gap-3">
          <TelecomLogo className="h-6 w-6 text-accent-active" />
          <div className="flex flex-col">
            <h1 className="text-sm font-semibold text-primary-text leading-tight">Kho Skill Agent</h1>
            <p className="text-[10px] text-secondary-text">testing · ready · rejected</p>
          </div>
        </div>
        <nav className="flex items-center gap-2">
          <Link
            className="flex items-center gap-1.5 rounded-lg border border-warm-border/60 bg-main-background/50 px-3 py-1.5 font-mono text-[11px] font-semibold uppercase tracking-wider text-secondary-text transition hover:border-accent-active/40 hover:bg-accent-active/5 hover:text-accent-active"
            href="/chat"
          >
            <ArrowLeftIcon className="h-3.5 w-3.5" /> Chat
          </Link>
          <Link
            className="flex items-center gap-1.5 rounded-lg border border-accent-active/30 bg-accent-active/5 px-3 py-1.5 font-mono text-[11px] font-semibold uppercase tracking-wider text-accent-active"
            href="/admin/skills"
          >
            <SkillsIcon className="h-3.5 w-3.5" /> Registry
          </Link>
          <Link
            className="flex items-center gap-1.5 rounded-lg border border-warm-border/60 bg-main-background/50 px-3 py-1.5 font-mono text-[11px] font-semibold uppercase tracking-wider text-secondary-text transition hover:border-accent-active/40 hover:bg-accent-active/5 hover:text-accent-active"
            href="/admin/skills/upload"
          >
            <UploadIcon className="h-3.5 w-3.5" /> Upload
          </Link>
        </nav>
      </header>

      {/* Main Split Content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left Side: Name + Tab list */}
        <div className="w-[360px] shrink-0 border-r border-warm-border flex flex-col bg-surface-card/25 overflow-hidden">

          {/* Status Tabs */}
          <div className="p-3 border-b border-warm-border shrink-0 bg-surface-card/20">
            <div className="grid grid-cols-3 rounded-lg border border-warm-border/70 bg-main-background/50 p-0.5">
              {TABS.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => {
                    setActiveTab(tab.id);
                    setSelected(null);
                  }}
                  className={`rounded-md py-1.5 text-[11px] font-semibold transition ${activeTab === tab.id
                      ? "bg-surface-card text-accent-active shadow-sm border border-warm-border/50"
                      : "text-secondary-text hover:text-primary-text"
                    }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>

          {/* Error Message if any */}
          {error ? (
            <div role="alert" className="m-3 mb-0 rounded-lg border border-status-crimson/40 bg-status-crimson/5 p-2 text-[11px] text-status-crimson shrink-0">
              {error}
            </div>
          ) : null}

          {/* Skill list */}
          <div className="flex-1 overflow-y-auto divide-y divide-warm-border/40">
            {visibleSkills.map((skill) => (
              <button
                key={skill.id}
                type="button"
                onClick={() => setSelected(skill)}
                className={`w-full text-left p-3.5 transition flex flex-col gap-1 hover:bg-accent-active/[0.03] outline-none focus-visible:bg-accent-active/[0.03] ${selected?.id === skill.id
                    ? "bg-accent-active/[0.06] border-r-2 border-accent-active"
                    : ""
                  }`}
              >
                <div className="flex items-start justify-between gap-2 w-full">
                  <span className="font-semibold text-xs text-primary-text break-all line-clamp-1">{skill.name}</span>
                  <span className="font-mono text-[10px] text-secondary-text shrink-0">v{skill.version}</span>
                </div>
                <span className="text-[11px] text-secondary-text line-clamp-2 leading-relaxed">{skill.description}</span>
                {skill.status === "ready" && (
                  <div className="mt-1.5 flex gap-2 font-mono text-[9px] text-secondary-text">
                    <span className="rounded bg-code-block/60 px-1.5 py-0.5">Calls: {skill.telemetry?.call_count ?? 0}</span>
                    <span className="rounded bg-code-block/60 px-1.5 py-0.5">Err: {formatErrorRate(skill.telemetry?.error_rate)}</span>
                  </div>
                )}
              </button>
            ))}
            {!visibleSkills.length && (
              <div className="p-8 text-center text-xs text-secondary-text">
                Không có skill nào ở trạng thái này.
              </div>
            )}
          </div>

          {/* Footer Refresh */}
          <div className="p-3 border-t border-warm-border shrink-0 bg-surface-card/20">
            <button
              type="button"
              onClick={() => void refresh()}
              className="w-full flex h-8 items-center justify-center gap-1.5 rounded-lg border border-warm-border bg-surface-card px-3 font-mono text-[10px] font-semibold uppercase tracking-wider text-secondary-text transition hover:border-accent-active/40 hover:bg-accent-active/5 hover:text-accent-active"
            >
              <RefreshIcon className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} />
              {loading ? "Đang tải..." : "Làm mới danh sách"}
            </button>
          </div>
        </div>

        {/* Right Side: Skill Detail or Placeholder */}
        <div className="flex-1 overflow-hidden flex flex-col bg-main-background relative">
          {selected ? (
            <SkillReviewPanel
              skill={selected}
              note={note}
              onNoteChange={setNote}
              onClose={() => setSelected(null)}
              onReview={(action) => void review(selected, action)}
            />
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center p-8 text-center bg-surface-card/5 select-none">
              <TelecomLogo className="h-12 w-12 text-secondary-text/30 mb-4 animate-pulse" />
              <h3 className="text-sm font-semibold text-secondary-text">Chưa chọn AI Skill</h3>
              <p className="mt-1.5 text-xs text-secondary-text/70 max-w-xs leading-relaxed">
                Hãy chọn một skill ở danh sách bên trái để kiểm tra chi tiết cấu trúc, mã nguồn, kết quả sandbox và thực hiện phê duyệt/cách ly.
              </p>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
