"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "@/lib/api";
import type { SkillStatus, SkillSummary } from "@/lib/types";
import { SkillReviewPanel } from "@/components/skill-review-panel";
import { ArrowLeftIcon, RefreshIcon, SkillsIcon, UploadIcon } from "@/components/icons";

const TABS: { id: SkillStatus; label: string }[] = [
  { id: "testing", label: "Chờ phê duyệt" },
  { id: "ready", label: "Đang kích hoạt" },
  { id: "rejected", label: "Cách ly" },
];

function statusTone(status: SkillStatus) {
  if (status === "ready") return "bg-status-sage/10 text-status-sage";
  if (status === "rejected") return "bg-status-crimson/10 text-status-crimson";
  return "bg-accent-active/10 text-accent-active";
}

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
    <main className="flex min-h-screen flex-col bg-main-background text-primary-text">
      <header className="flex h-14 items-center justify-between border-b border-warm-border px-5">
        <div>
          <h1 className="text-sm font-semibold">Vòng đời vũ khí AI</h1>
          <p className="text-xs text-secondary-text">testing · ready · rejected</p>
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

      <section className="mx-auto w-full max-w-6xl flex-1 p-6">
        <div className="mb-4 flex items-center justify-between gap-4">
          <div className="grid w-full max-w-xl grid-cols-3 rounded-lg border border-warm-border bg-surface-card p-1">
            {TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`rounded-md px-3 py-2 text-xs font-semibold transition ${
                  activeTab === tab.id
                    ? "bg-code-block text-accent-active shadow-sm"
                    : "text-secondary-text hover:text-primary-text"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => void refresh()}
            className="flex h-10 items-center justify-center gap-1.5 rounded-lg border border-warm-border bg-surface-card px-4 font-mono text-xs font-semibold uppercase tracking-wider text-secondary-text transition hover:border-accent-active/40 hover:bg-accent-active/5 hover:text-accent-active hover:shadow-sm"
          >
            <RefreshIcon className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} /> {loading ? "Đang tải..." : "Refresh"}
          </button>
        </div>

        {error ? (
          <div role="alert" className="mb-4 rounded-lg border border-status-crimson/40 bg-status-crimson/5 p-3 text-sm text-status-crimson">
            {error}
          </div>
        ) : null}

        <div className="overflow-hidden rounded-lg border border-warm-border bg-surface-card">
          <table className="w-full border-collapse text-left text-sm">
            <thead className="border-b border-warm-border bg-code-block/70 text-xs uppercase text-secondary-text">
              <tr>
                <th className="px-4 py-3 font-semibold">Skill</th>
                <th className="px-4 py-3 font-semibold">Version</th>
                <th className="px-4 py-3 font-semibold">Status</th>
                <th className="px-4 py-3 font-semibold">Telemetry</th>
              </tr>
            </thead>
            <tbody>
              {visibleSkills.map((skill) => (
                <tr
                  key={skill.id}
                  onClick={() => setSelected(skill)}
                  className="cursor-pointer border-b border-warm-border last:border-b-0 hover:bg-main-background"
                >
                  <td className="px-4 py-3">
                    <p className="font-medium">{skill.name}</p>
                    <p className="mt-1 line-clamp-1 text-xs text-secondary-text">{skill.description}</p>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">{skill.version}</td>
                  <td className="px-4 py-3">
                    <span className={`rounded px-2 py-1 text-xs font-semibold ${statusTone(skill.status)}`}>
                      {skill.status}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {skill.status === "ready" ? (
                      <div className="grid min-w-36 grid-cols-2 gap-2 text-xs">
                        <span className="rounded bg-code-block px-2 py-1">
                          <span className="block text-[10px] uppercase text-secondary-text">calls</span>
                          <span className="font-mono">{skill.telemetry?.call_count ?? 0}</span>
                        </span>
                        <span className="rounded bg-code-block px-2 py-1">
                          <span className="block text-[10px] uppercase text-secondary-text">err</span>
                          <span className="font-mono">
                            {formatErrorRate(skill.telemetry?.error_rate)}
                          </span>
                        </span>
                      </div>
                    ) : (
                      <span className="text-xs text-secondary-text">Audit logs available</span>
                    )}
                  </td>
                </tr>
              ))}
              {!visibleSkills.length ? (
                <tr>
                  <td colSpan={4} className="px-4 py-12 text-center text-sm text-secondary-text">
                    Không có skill nào trong tab này.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      {selected ? (
        <SkillReviewPanel
          skill={selected}
          note={note}
          onNoteChange={setNote}
          onClose={() => setSelected(null)}
          onReview={(action) => void review(selected, action)}
        />
      ) : null}
    </main>
  );
}
