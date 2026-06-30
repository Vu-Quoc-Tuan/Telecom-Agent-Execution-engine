"use client";

import type { ReactNode } from "react";
import { useState } from "react";

type MarkdownMessageProps = {
  content: string;
};

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      aria-label="Copy code block"
      className="absolute right-2 top-2 rounded-md border border-white/15 bg-white/10 px-2 py-1 text-[11px] font-medium text-terminal-fg/80 transition hover:border-accent-active hover:text-terminal-fg"
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function safeHref(href: string) {
  const trimmed = href.trim();
  if (/^(https?:|mailto:)/i.test(trimmed)) return trimmed;
  if (trimmed.startsWith("#")) return trimmed;
  return null;
}

function renderInline(text: string): ReactNode[] {
  const parts = text.split(
    /(`[^`\n]+`|\*\*[^*\n]+?\*\*|(?<![\w./-])\*(?![\s*.])[^*\n]*?[^\s*]\*(?![\w./-])|\[[^\]\n]+\]\([^)]+\))/g,
  );
  return parts.map((part, index) => {
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code
          key={`${part}-${index}`}
          className="rounded-md border border-warm-border bg-code-block px-1.5 py-0.5 font-mono text-[0.85em] text-primary-text"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={`${part}-${index}`} className="font-semibold text-current">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("*") && part.endsWith("*")) {
      return (
        <em key={`${part}-${index}`} className="italic text-current">
          {part.slice(1, -1)}
        </em>
      );
    }
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (link) {
      const href = safeHref(link[2]);
      if (href) {
        return (
          <a
            key={`${part}-${index}`}
            href={href}
            target={href.startsWith("#") ? undefined : "_blank"}
            rel={href.startsWith("#") ? undefined : "noreferrer"}
            className="font-medium text-accent-active underline decoration-accent-active/40 underline-offset-4 hover:decoration-accent-active"
          >
            {link[1]}
          </a>
        );
      }
    }
    return <span key={`${part}-${index}`}>{part}</span>;
  });
}

function splitTableRow(line: string) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function tableAlignments(separatorLine: string) {
  return splitTableRow(separatorLine).map((cell) => {
    const trimmed = cell.trim();
    if (trimmed.startsWith(":") && trimmed.endsWith(":")) return "text-center";
    if (trimmed.endsWith(":")) return "text-right";
    return "text-left";
  });
}

function parseTextBlocks(text: string): ReactNode[] {
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];

  let i = 0;
  while (i < lines.length) {
    const startIndex = i;
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      i++;
      continue;
    }

    // 1. Horizontal Rule
    if (/^---+$/.test(trimmed) || /^\*\*\*+$/.test(trimmed)) {
      blocks.push(<hr key={`hr-${startIndex}`} className="my-4 border-warm-border" />);
      i++;
      continue;
    }

    // 2. Heading
    const headingMatch = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      const Tag = level === 1 ? "h2" : level === 2 ? "h3" : "h4";
      const headingClass =
        level === 1
          ? "border-b border-warm-border pb-1 text-lg font-bold text-primary-text mt-4 mb-2"
          : level === 2
            ? "border-l-2 border-accent-active pl-2 text-base font-bold text-primary-text mt-4 mb-2"
            : "text-sm font-semibold text-primary-text mt-3 mb-1.5";

      blocks.push(
        <Tag key={`heading-${startIndex}`} className={headingClass}>
          {renderInline(headingMatch[2])}
        </Tag>
      );
      i++;
      continue;
    }

    // 3. Blockquote
    if (trimmed.startsWith(">")) {
      const quoteLines: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) {
        quoteLines.push(lines[i].trim().replace(/^>\s?/, ""));
        i++;
      }
      blocks.push(
        <blockquote key={`blockquote-${startIndex}`} className="rounded-r-lg border-l-4 border-accent-active bg-accent-active/5 px-4 py-3 text-sm leading-6 text-secondary-text my-2">
          {quoteLines.map((l, idx) => (
            <span key={idx}>
              {renderInline(l)}
              {idx < quoteLines.length - 1 ? <br /> : null}
            </span>
          ))}
        </blockquote>
      );
      continue;
    }

    // 4. Table
    if (line.includes("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[i + 1])) {
      const headerLine = line;
      const separatorLine = lines[i + 1];
      const headers = splitTableRow(headerLine);
      const alignments = tableAlignments(separatorLine);
      const rows: string[][] = [];

      i += 2;
      while (i < lines.length && lines[i].includes("|")) {
        rows.push(splitTableRow(lines[i]));
        i++;
      }

      blocks.push(
        <div key={`table-${startIndex}`} className="max-w-full overflow-hidden rounded-lg border border-warm-border bg-surface-card shadow-sm my-3">
          <div className="overflow-x-auto">
            <table className="w-full min-w-max border-collapse text-sm">
              <thead className="bg-code-block/90 text-[11px] uppercase text-secondary-text">
                <tr>
                  {headers.map((header, cellIndex) => (
                    <th
                      key={cellIndex}
                      className={`border-b border-warm-border px-3 py-2.5 font-semibold ${
                        alignments[cellIndex] ?? "text-left"
                      }`}
                    >
                      {renderInline(header)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-warm-border">
                {rows.map((row, rowIndex) => (
                  <tr
                    key={rowIndex}
                    className="odd:bg-main-background/40 hover:bg-code-block/60"
                  >
                    {headers.map((_, cellIndex) => (
                      <td
                        key={cellIndex}
                        className={`px-3 py-2.5 align-top text-primary-text ${
                          alignments[cellIndex] ?? "text-left"
                        }`}
                      >
                        {renderInline(row[cellIndex] ?? "")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      );
      continue;
    }

    // 5. Unordered List
    if (/^[-*]\s+/.test(trimmed)) {
      const listItems: string[] = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
        listItems.push(lines[i].trim().replace(/^[-*]\s+/, ""));
        i++;
      }
      blocks.push(
        <ul key={`ul-${startIndex}`} className="list-disc space-y-1.5 pl-5 text-sm leading-6 marker:text-accent-active my-2">
          {listItems.map((item, idx) => (
            <li key={idx}>{renderInline(item)}</li>
          ))}
        </ul>
      );
      continue;
    }

    // 6. Ordered List
    if (/^\d+\.\s+/.test(trimmed)) {
      const listItems: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
        listItems.push(lines[i].trim().replace(/^\d+\.\s+/, ""));
        i++;
      }
      blocks.push(
        <ol key={`ol-${startIndex}`} className="list-decimal space-y-1.5 pl-5 text-sm leading-6 marker:font-semibold marker:text-accent-active my-2">
          {listItems.map((item, idx) => (
            <li key={idx}>{renderInline(item)}</li>
          ))}
        </ol>
      );
      continue;
    }

    // 7. Paragraph
    const paraLines: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^---+$/.test(lines[i].trim()) &&
      !/^\*\*\*+$/.test(lines[i].trim()) &&
      !/^(#{1,4})\s+/.test(lines[i].trim()) &&
      !lines[i].trim().startsWith(">") &&
      !/^[-*]\s+/.test(lines[i].trim()) &&
      !/^\d+\.\s+/.test(lines[i].trim()) &&
      !(lines[i].includes("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[i + 1]))
    ) {
      paraLines.push(lines[i]);
      i++;
    }

    blocks.push(
      <p key={`p-${startIndex}`} className="text-sm leading-7 text-primary-text/90 my-2">
        {paraLines.map((line, idx) => (
          <span key={idx}>
            {renderInline(line)}
            {idx < paraLines.length - 1 ? <br /> : null}
          </span>
        ))}
      </p>
    );
  }

  return blocks;
}

export function MarkdownMessage({ content }: MarkdownMessageProps) {
  const blocks = content.split(/```/g);

  if (!content.trim()) {
    return <span className="inline-block h-4 w-4 animate-pulse rounded-full bg-accent-active/70" />;
  }

  return (
    <div className="message-markdown min-w-0 space-y-3 break-words">
      {blocks.map((block, index) => {
        if (index % 2 === 1) {
          const [firstLine, ...rest] = block.replace(/^\n/, "").split("\n");
          const language = firstLine.trim() && !firstLine.includes(" ") ? firstLine.trim() : "";
          const code = language ? rest.join("\n") : block;
          return (
            <div key={`${index}-${code.slice(0, 12)}`} className="relative">
              <CopyButton value={code.trimEnd()} />
              <pre className="max-w-full overflow-x-auto rounded-lg border border-[#36332d] bg-terminal-bg p-4 pr-20 font-mono text-sm leading-6 text-terminal-fg shadow-sm">
                {language ? (
                  <span className="mb-3 inline-flex rounded-md border border-white/10 bg-white/10 px-2 py-1 text-[11px] uppercase text-terminal-fg/70">
                    {language}
                  </span>
                ) : null}
                <code>{code.trimEnd()}</code>
              </pre>
            </div>
          );
        }

        return <div key={index}>{parseTextBlocks(block)}</div>;
      })}
    </div>
  );
}
