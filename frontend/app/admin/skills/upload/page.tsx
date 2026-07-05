"use client";

import Link from "next/link";
import { ChangeEvent, FormEvent, useMemo, useState } from "react";
import { apiUrl } from "@/lib/api";
import { ArrowLeftIcon, SkillsIcon, UploadIcon, TelecomLogo } from "@/components/icons";

type UploadResult = {
  status: string;
  message: string;
  skill_id?: string;
  pipeline_audit_logs?: string[];
};

type SkillPackagePreview = {
  name: string;
  description: string;
  frontmatter: Record<string, unknown>;
  files: Array<{
    path: string;
    encoding: string;
    media_type: string;
    size: number;
  }>;
};

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

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export default function SkillUploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<SkillPackagePreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [inspecting, setInspecting] = useState(false);
  const [running, setRunning] = useState(false);

  const consoleLines = useMemo(() => {
    if (running) {
      return [
        "[RUNNING] Upload package tới backend.",
        "[RUNNING] Parser đọc SKILL.md, frontmatter và bundled files từ zip.",
        "[RUNNING] Sandbox đang chạy static scan, domain judge và review gate.",
      ];
    }
    if (result?.pipeline_audit_logs?.length) return result.pipeline_audit_logs;
    if (inspecting) {
      return [
        "[INSPECT] Đang đọc zip package.",
        "[INSPECT] Backend parse SKILL.md để lấy metadata nội bộ.",
      ];
    }
    if (preview) {
      return [
        `[READY] ${preview.files.length} bundled file(s) sẽ đi cùng skill.`,
        "[READY] Bấm thẩm định để chạy pipeline bảo mật đầy đủ.",
      ];
    }
    return [
      "[IDLE] Chờ gói Agent Skill .zip.",
      "[INFO] UI không nhận metadata nhập tay; backend đọc trực tiếp từ SKILL.md trong zip.",
    ];
  }, [inspecting, preview, result, running]);

  async function inspectFile(selectedFile: File) {
    setInspecting(true);
    setPreview(null);
    setPreviewError(null);
    const body = new FormData();
    body.append("file", selectedFile);

    try {
      const response = await fetch(apiUrl("/skills/inspect"), { method: "POST", body });
      const data = await response.json();
      if (!response.ok) {
        setPreviewError(normalizeErrorPayload(data).message);
        return;
      }
      setPreview(data as SkillPackagePreview);
    } catch (exc) {
      setPreviewError(exc instanceof Error ? exc.message : "Không đọc được metadata trong zip.");
    } finally {
      setInspecting(false);
    }
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const selectedFile = event.target.files?.[0] ?? null;
    setFile(selectedFile);
    setResult(null);
    setPreview(null);
    setPreviewError(null);

    if (!selectedFile) return;
    if (!selectedFile.name.toLowerCase().endsWith(".zip")) {
      setPreviewError("Skill package phải là file .zip.");
      return;
    }
    void inspectFile(selectedFile);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setRunning(true);
    setResult(null);

    if (!file) {
      setRunning(false);
      setResult({ status: "FAILED", message: "Chọn file .zip trước khi chạy pipeline." });
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

  const submitDisabled = running || inspecting || !file || Boolean(previewError);

  return (
    <main className="flex min-h-screen flex-col bg-main-background text-primary-text">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-warm-border bg-surface-card px-5 z-10">
        <div className="flex items-center gap-3">
          <TelecomLogo className="h-6 w-6 text-accent-active" />
          <div className="flex flex-col">
            <h1 className="text-sm font-semibold text-primary-text leading-tight">Thẩm định Skill Agent</h1>
            <p className="text-[10px] text-secondary-text">zip package · metadata preview · sandbox review</p>
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
            className="flex items-center gap-1.5 rounded-lg border border-warm-border/60 bg-main-background/50 px-3 py-1.5 font-mono text-[11px] font-semibold uppercase tracking-wider text-secondary-text transition hover:border-accent-active/40 hover:bg-accent-active/5 hover:text-accent-active"
            href="/admin/skills"
          >
            <SkillsIcon className="h-3.5 w-3.5" /> Registry
          </Link>
          <Link
            className="flex items-center gap-1.5 rounded-lg border border-accent-active/30 bg-accent-active/5 px-3 py-1.5 font-mono text-[11px] font-semibold uppercase tracking-wider text-accent-active"
            href="/admin/skills/upload"
          >
            <UploadIcon className="h-3.5 w-3.5" /> Upload
          </Link>
        </nav>
      </header>

      <form onSubmit={submit} className="grid min-h-0 flex-1 gap-6 p-6 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <section className="min-h-0 space-y-4 overflow-y-auto">
          <label className="block rounded-lg border border-dashed border-warm-border bg-surface-card p-5">
            <span className="text-xs font-semibold uppercase text-secondary-text">Gói skill .zip</span>
            <input
              type="file"
              accept=".zip,application/zip"
              onChange={handleFileChange}
              className="mt-3 block w-full rounded-lg border border-warm-border bg-main-background px-3 py-2 text-sm"
            />
            {file ? (
              <div className="mt-4 rounded-lg bg-code-block p-3 text-sm">
                <p className="font-medium">{file.name}</p>
                <p className="mt-1 font-mono text-xs text-secondary-text">{formatBytes(file.size)}</p>
              </div>
            ) : null}
          </label>

          {previewError ? (
            <div role="alert" className="rounded-lg border border-status-crimson/40 bg-status-crimson/5 p-4 text-sm text-status-crimson">
              {previewError}
            </div>
          ) : null}

          {preview ? (
            <section className="rounded-lg border border-warm-border bg-surface-card p-5">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-xs font-semibold uppercase text-secondary-text">Metadata từ SKILL.md</p>
                  <h2 className="mt-2 truncate text-lg font-semibold">{preview.name}</h2>
                  <p className="mt-2 text-sm leading-6 text-secondary-text">{preview.description}</p>
                </div>
              </div>

              <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2">
                <div className="rounded-lg border border-warm-border p-3">
                  <dt className="text-xs uppercase text-secondary-text">Bundled files</dt>
                  <dd className="mt-1 font-mono">{preview.files.length}</dd>
                </div>
                <div className="rounded-lg border border-warm-border p-3">
                  <dt className="text-xs uppercase text-secondary-text">Allowed tools</dt>
                  <dd className="mt-1 truncate font-mono text-xs">
                    {String(preview.frontmatter["allowed-tools"] ?? "not declared")}
                  </dd>
                </div>
              </dl>

              {preview.files.length ? (
                <div className="mt-5 overflow-hidden rounded-lg border border-warm-border">
                  <table className="w-full border-collapse text-left text-xs">
                    <thead className="bg-code-block text-secondary-text">
                      <tr>
                        <th className="px-3 py-2 font-semibold">File</th>
                        <th className="px-3 py-2 font-semibold">Type</th>
                        <th className="px-3 py-2 text-right font-semibold">Size</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.files.map((entry) => (
                        <tr key={entry.path} className="border-t border-warm-border">
                          <td className="max-w-0 truncate px-3 py-2 font-mono">{entry.path}</td>
                          <td className="px-3 py-2 text-secondary-text">{entry.media_type}</td>
                          <td className="px-3 py-2 text-right font-mono">{formatBytes(entry.size)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </section>
          ) : (
            <div className="rounded-lg border border-warm-border bg-surface-card p-5 text-sm text-secondary-text">
              Metadata sẽ xuất hiện ở đây sau khi backend đọc `SKILL.md` trong zip.
            </div>
          )}
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
                className={line.includes("PASS") || line.includes("Passed") || line.includes("READY") ? "text-status-sage" : ""}
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
              disabled={submitDisabled}
              className="h-11 w-full rounded-lg bg-accent-active text-sm font-semibold text-white transition hover:bg-[#b86205] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {running ? "Đang thẩm định..." : inspecting ? "Đang đọc package..." : "Kích nổ Thẩm định Pipeline"}
            </button>
          </div>
        </section>
      </form>
    </main>
  );
}
