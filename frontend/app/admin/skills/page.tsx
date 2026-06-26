"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "@/lib/api";
import type { SkillStatus, SkillSummary } from "@/lib/types";

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
        <nav className="flex items-center gap-2 text-sm">
          <Link className="rounded-md px-3 py-2 hover:bg-surface-card" href="/chat">
            Chat
          </Link>
          <Link className="rounded-md px-3 py-2 hover:bg-surface-card" href="/admin/skills/upload">
            Upload
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
                className={`rounded-md px-3 py-2 text-xs font-semibold ${
                  activeTab === tab.id ? "bg-code-block" : "text-secondary-text"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => void refresh()}
            className="h-10 rounded-lg border border-warm-border bg-surface-card px-4 text-sm font-medium hover:bg-code-block"
          >
            {loading ? "Đang tải..." : "Refresh"}
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
                      <div className="grid grid-cols-3 gap-2 text-xs">
                        <span className="rounded bg-code-block px-2 py-1">calls: 0</span>
                        <span className="rounded bg-code-block px-2 py-1">avg: n/a</span>
                        <span className="rounded bg-code-block px-2 py-1">err: 0%</span>
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
        <div className="fixed inset-0 z-20 flex justify-end bg-black/20" onClick={() => setSelected(null)}>
          <aside
            className="h-full w-full max-w-xl overflow-y-auto border-l border-warm-border bg-surface-card p-5 shadow-xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className={`mb-2 inline-block rounded px-2 py-1 text-xs font-semibold ${statusTone(selected.status)}`}>
                  {selected.status}
                </p>
                <h2 className="text-xl font-semibold">{selected.name}</h2>
                <p className="mt-2 text-sm leading-6 text-secondary-text">{selected.description}</p>
              </div>
              <button
                type="button"
                onClick={() => setSelected(null)}
                aria-label="Đóng chi tiết skill"
                className="rounded-md px-3 py-2 text-xl hover:bg-code-block"
              >
                ×
              </button>
            </div>

            <div className="mt-5 grid grid-cols-2 gap-3 text-sm">
              <div className="rounded-lg border border-warm-border p-3">
                <p className="text-xs uppercase text-secondary-text">Version</p>
                <p className="mt-1 font-mono">{selected.version}</p>
              </div>
              <div className="rounded-lg border border-warm-border p-3">
                <p className="text-xs uppercase text-secondary-text">Malicious</p>
                <p className="mt-1">{selected.is_malicious ? "Yes" : "No"}</p>
              </div>
            </div>

            <section className="mt-5">
              <h3 className="text-sm font-semibold">Audit logs 5 lớp</h3>
              <pre className="mt-2 max-h-80 overflow-auto rounded-lg bg-terminal-bg p-4 font-mono text-xs leading-5 text-terminal-fg">
                {selected.security_review_log || "Backend chưa có audit log cho skill này."}
              </pre>
            </section>

            <label className="mt-5 block">
              <span className="text-sm font-semibold">Ghi chú admin</span>
              <textarea
                value={note}
                onChange={(event) => setNote(event.target.value)}
                className="mt-2 min-h-24 w-full resize-none rounded-lg border border-warm-border p-3 text-sm"
                placeholder="Lý do approve/reject hoặc lock khẩn cấp..."
              />
            </label>

            <div className="mt-4 grid grid-cols-2 gap-3">
              {selected.status === "testing" ? (
                <button
                  type="button"
                  onClick={() => void review(selected, "approve")}
                  className="h-11 rounded-lg bg-accent-active text-sm font-semibold text-white hover:bg-[#b86205]"
                >
                  Chuyển Production
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => void review(selected, "reject")}
                className="h-11 rounded-lg bg-status-crimson text-sm font-semibold text-white hover:bg-[#dc2626]"
              >
                {selected.status === "ready" ? "KHÓA KHẨN CẤP" : "Từ chối / cách ly"}
              </button>
            </div>
          </aside>
        </div>
      ) : null}
    </main>
  );
}
