"use client";

import type { SkillScriptManifestEntry, SkillSummary } from "@/lib/types";
import { CheckIcon, CloseIcon } from "@/components/icons";

type ReviewAction = "approve" | "reject";

type SkillReviewPanelProps = {
  skill: SkillSummary;
  note: string;
  onNoteChange: (note: string) => void;
  onClose: () => void;
  onReview: (action: ReviewAction) => void;
};

function statusTone(status: SkillSummary["status"] | string) {
  if (status === "ready" || status === "passed") return "bg-status-sage/10 text-status-sage";
  if (status === "rejected" || status === "failed") return "bg-status-crimson/10 text-status-crimson";
  return "bg-accent-active/10 text-accent-active";
}

function formatJson(value: unknown) {
  return JSON.stringify(value ?? {}, null, 2);
}

function manifestEntries(skill: SkillSummary) {
  return Object.entries(skill.script_manifest ?? {}).sort(([left], [right]) =>
    left.localeCompare(right),
  );
}

function numberValue(object: Record<string, unknown> | undefined, key: string) {
  const value = object?.[key];
  return typeof value === "number" ? value : null;
}

function booleanLabel(value: unknown) {
  if (value === true) return "Yes";
  if (value === false) return "No";
  return "n/a";
}

function CodeBlock({ children }: { children: string }) {
  return (
    <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words bg-terminal-bg p-3 font-mono text-[11px] leading-5 text-terminal-fg">
      {children || "(empty)"}
    </pre>
  );
}

function ScriptEvidence({
  path,
  entry,
  skill,
}: {
  path: string;
  entry: SkillScriptManifestEntry;
  skill: SkillSummary;
}) {
  const source = skill.bundled_files?.[path];
  const sandbox = entry.sandbox_result;
  const timeout = numberValue(entry.limits, "timeout_seconds");
  const exitCode = numberValue(sandbox, "exit_code");
  const stdout = typeof sandbox?.stdout_preview === "string" ? sandbox.stdout_preview : "";
  const stderr = typeof sandbox?.stderr === "string" ? sandbox.stderr : "";
  const contractError =
    typeof sandbox?.output_contract_error === "string" ? sandbox.output_contract_error : "";

  return (
    <details className="border-b border-warm-border last:border-b-0">
      <summary className="cursor-pointer list-none px-3 py-3 hover:bg-main-background focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-active">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="break-all font-mono text-xs font-semibold">{path}</p>
            <p className="mt-1 text-xs text-secondary-text">{entry.purpose || "No purpose provided"}</p>
          </div>
          <span className={`shrink-0 rounded px-2 py-1 text-[10px] font-semibold ${statusTone(entry.status ?? "unknown")}`}>
            {entry.status ?? "unknown"}
          </span>
        </div>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] text-secondary-text">
          <span>exit: {exitCode ?? "n/a"}</span>
          <span>timeout: {timeout ? `${timeout}s` : "default"}</span>
          <span>timed out: {booleanLabel(sandbox?.timed_out)}</span>
        </div>
      </summary>

      <div className="space-y-4 border-t border-warm-border bg-main-background p-3">
        <section>
          <h4 className="mb-2 text-xs font-semibold uppercase text-secondary-text">Script source</h4>
          {source?.encoding === "utf-8" && typeof source.content === "string" ? (
            <CodeBlock>{source.content}</CodeBlock>
          ) : (
            <p className="text-xs text-status-crimson">UTF-8 source is unavailable.</p>
          )}
        </section>

        <div className="grid gap-4 lg:grid-cols-2">
          <section>
            <h4 className="mb-2 text-xs font-semibold uppercase text-secondary-text">Input schema</h4>
            <CodeBlock>{formatJson(entry.input_schema)}</CodeBlock>
          </section>
          <section>
            <h4 className="mb-2 text-xs font-semibold uppercase text-secondary-text">Smoke arguments</h4>
            <CodeBlock>{formatJson(entry.smoke_test?.arguments)}</CodeBlock>
          </section>
          <section>
            <h4 className="mb-2 text-xs font-semibold uppercase text-secondary-text">Output contract</h4>
            <CodeBlock>{formatJson(entry.output_contract)}</CodeBlock>
          </section>
          <section>
            <h4 className="mb-2 text-xs font-semibold uppercase text-secondary-text">Artifact identity</h4>
            <dl className="space-y-2 bg-code-block p-3 text-xs">
              <div>
                <dt className="text-secondary-text">SHA-256</dt>
                <dd className="mt-1 break-all font-mono">{entry.script_hash || "n/a"}</dd>
              </div>
              <div>
                <dt className="text-secondary-text">Runtime</dt>
                <dd className="mt-1 font-mono">{formatJson(entry.runtime)}</dd>
              </div>
            </dl>
          </section>
        </div>

        <section>
          <h4 className="mb-2 text-xs font-semibold uppercase text-secondary-text">Sandbox result</h4>
          {contractError ? <p className="mb-2 text-xs text-status-crimson">{contractError}</p> : null}
          <div className="grid gap-3 lg:grid-cols-2">
            <div>
              <p className="mb-1 text-[10px] uppercase text-secondary-text">stdout</p>
              <CodeBlock>{stdout}</CodeBlock>
            </div>
            <div>
              <p className="mb-1 text-[10px] uppercase text-secondary-text">stderr</p>
              <CodeBlock>{stderr}</CodeBlock>
            </div>
          </div>
        </section>
      </div>
    </details>
  );
}

export function SkillReviewPanel({
  skill,
  note,
  onNoteChange,
  onClose,
  onReview,
}: SkillReviewPanelProps) {
  const scripts = manifestEntries(skill);
  const files = Object.entries(skill.bundled_files ?? {}).sort(([left], [right]) =>
    left.localeCompare(right),
  );
  const allScriptsPassed = scripts.every(([, entry]) => entry.status === "passed");

  return (
    <div
      role="region"
      aria-label={`Review skill ${skill.name}`}
      className="flex flex-col h-full w-full bg-surface-card"
    >
      <header className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-warm-border bg-surface-card px-5 py-4 shrink-0">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <span className={`rounded px-2 py-1 text-xs font-semibold ${statusTone(skill.status)}`}>
              {skill.status}
            </span>
          </div>
          <h2 className="break-words text-lg font-semibold">{skill.name}</h2>
          <p className="mt-1 text-sm leading-5 text-secondary-text">{skill.description}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close skill review"
          className="rounded-md p-2 text-secondary-text transition hover:bg-code-block hover:text-primary-text"
        >
          <CloseIcon className="h-5 w-5" />
        </button>
      </header>

      <div className="flex-1 overflow-y-auto space-y-6 p-5">
        <section>
          <h3 className="mb-2 text-sm font-semibold">SKILL.md instructions</h3>
          <CodeBlock>{skill.skill_md}</CodeBlock>
        </section>

        <section>
          <div className="mb-2 flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold">Package inventory</h3>
            <span className="font-mono text-xs text-secondary-text">{files.length} files</span>
          </div>
          <div className="overflow-hidden border border-warm-border">
            {files.length ? files.map(([path, file]) => (
              <div key={path} className="flex items-center justify-between gap-3 border-b border-warm-border px-3 py-2 text-xs last:border-b-0">
                <span className="min-w-0 break-all font-mono">{path}</span>
                <span className="shrink-0 text-secondary-text">{file.media_type || file.encoding || "unknown"} · {file.size ?? 0} B</span>
              </div>
            )) : (
              <p className="p-3 text-sm text-secondary-text">No bundled resources.</p>
            )}
          </div>
        </section>

        <section>
          <div className="mb-2 flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold">Script validation evidence</h3>
            {scripts.length > 0 && allScriptsPassed ? (
              <span className="flex items-center gap-1 text-xs font-semibold text-status-sage">
                <CheckIcon className="h-3.5 w-3.5" /> All passed
              </span>
            ) : null}
          </div>
          {scripts.length ? (
            <div className="overflow-hidden border border-warm-border">
              {scripts.map(([path, entry]) => (
                <ScriptEvidence key={path} path={path} entry={entry} skill={skill} />
              ))}
            </div>
          ) : (
            <p className="border border-warm-border p-3 text-sm text-secondary-text">
              This skill has no runnable Python scripts.
            </p>
          )}
        </section>

        <section>
          <h3 className="mb-2 text-sm font-semibold">Validation audit log</h3>
          <CodeBlock>{skill.security_review_log || "Backend has not recorded an audit log."}</CodeBlock>
        </section>

        <label className="block">
          <span className="text-sm font-semibold">Reviewer note</span>
          <textarea
            value={note}
            onChange={(event) => onNoteChange(event.target.value)}
            className="mt-2 min-h-24 w-full resize-y rounded-md border border-warm-border bg-main-background p-3 text-sm"
            placeholder="Reason for approving or rejecting this package"
          />
        </label>
      </div>

      <footer className="sticky bottom-0 grid grid-cols-2 gap-3 border-t border-warm-border bg-surface-card p-4 shrink-0">
        {skill.status === "testing" ? (
          <button
            type="button"
            onClick={() => onReview("approve")}
            disabled={!allScriptsPassed}
            title={!allScriptsPassed ? "Every script must pass validation before approval" : undefined}
            className="h-11 rounded-md bg-accent-active text-sm font-semibold text-white hover:bg-[#b86205] disabled:cursor-not-allowed disabled:opacity-40"
          >
            Approve package
          </button>
        ) : <span />}
        <button
          type="button"
          onClick={() => onReview("reject")}
          className="h-11 rounded-md bg-status-crimson text-sm font-semibold text-white hover:bg-[#dc2626]"
        >
          {skill.status === "ready" ? "Emergency lock" : "Reject package"}
        </button>
      </footer>
    </div>
  );
}
