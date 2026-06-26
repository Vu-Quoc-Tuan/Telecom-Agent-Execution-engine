"use client";

import { useState } from "react";

type MarkdownMessageProps = {
  content: string;
};

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }

  return (
    <button
      type="button"
      onClick={copy}
      className="absolute right-2 top-2 rounded-md border border-warm-border bg-surface-card px-2 py-1 text-[11px] font-medium text-secondary-text transition hover:border-accent-active hover:text-primary-text"
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function renderInline(text: string) {
  const parts = text.split(/(`[^`]+`)/g);
  return parts.map((part, index) => {
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code
          key={`${part}-${index}`}
          className="rounded bg-code-block px-1.5 py-0.5 font-mono text-[0.85em] text-primary-text"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    return <span key={`${part}-${index}`}>{part}</span>;
  });
}

export function MarkdownMessage({ content }: MarkdownMessageProps) {
  const blocks = content.split(/```/g);

  if (!content.trim()) {
    return <span className="inline-block h-4 w-4 animate-pulse rounded-full bg-accent-active/70" />;
  }

  return (
    <div className="message-markdown space-y-3">
      {blocks.map((block, index) => {
        if (index % 2 === 1) {
          const [firstLine, ...rest] = block.replace(/^\n/, "").split("\n");
          const language = firstLine.trim() && !firstLine.includes(" ") ? firstLine.trim() : "";
          const code = language ? rest.join("\n") : block;
          return (
            <div key={`${index}-${code.slice(0, 12)}`} className="relative">
              <CopyButton value={code.trimEnd()} />
              <pre className="overflow-x-auto rounded-lg border border-warm-border bg-code-block p-4 pr-20 font-mono text-sm leading-6 text-primary-text">
                {language ? (
                  <span className="mb-2 block text-[11px] uppercase text-secondary-text">
                    {language}
                  </span>
                ) : null}
                <code>{code.trimEnd()}</code>
              </pre>
            </div>
          );
        }

        return block
          .split(/\n{2,}/g)
          .filter(Boolean)
          .map((paragraph, paragraphIndex) => {
            const lines = paragraph.split("\n");
            const isList = lines.every((line) => /^[-*]\s+/.test(line.trim()));
            if (isList) {
              return (
                <ul
                  key={`${index}-${paragraphIndex}`}
                  className="list-disc space-y-1 pl-5 text-sm leading-6"
                >
                  {lines.map((line) => (
                    <li key={line}>{renderInline(line.replace(/^[-*]\s+/, ""))}</li>
                  ))}
                </ul>
              );
            }
            return (
              <p key={`${index}-${paragraphIndex}`} className="text-sm leading-6">
                {lines.map((line, lineIndex) => (
                  <span key={`${line}-${lineIndex}`}>
                    {renderInline(line)}
                    {lineIndex < lines.length - 1 ? <br /> : null}
                  </span>
                ))}
              </p>
            );
          });
      })}
    </div>
  );
}
