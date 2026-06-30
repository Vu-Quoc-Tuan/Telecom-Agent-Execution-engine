"use client";

import { useEffect, useRef, useState } from "react";
import type { ChatModelOption, ChatSkillOption } from "@/lib/types";
import { ChevronDownIcon, CheckIcon } from "@/components/icons";

type ModelSelection = Pick<ChatModelOption, "provider" | "model">;

function isSelectedModel(option: ChatModelOption, selected: ModelSelection | null) {
  return option.provider === selected?.provider && option.model === selected.model;
}

function Checkmark({ visible }: { visible: boolean }) {
  return (
    <span className="flex h-5 w-5 shrink-0 items-center justify-center text-accent-active" aria-hidden>
      {visible ? <CheckIcon className="h-4 w-4" /> : null}
    </span>
  );
}

export function ModelPicker({
  models,
  selectedModel,
  disabled,
  onSelectModel,
}: {
  models: ChatModelOption[];
  selectedModel: ModelSelection | null;
  disabled?: boolean;
  onSelectModel: (model: ModelSelection) => void;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const selectedModelOption = models.find((option) => isSelectedModel(option, selectedModel));
  const availableModelCount = models.filter((model) => model.available).length;

  useEffect(() => {
    if (!open) return;

    function closeOnOutsidePress(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    }

    document.addEventListener("pointerdown", closeOnOutsidePress);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePress);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return (
    <div ref={containerRef} className="relative min-w-0">
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Chọn model"
        className="flex h-9 max-w-[min(360px,70vw)] items-center gap-2 rounded-lg px-2.5 text-xs font-medium text-primary-text transition hover:bg-main-background disabled:cursor-not-allowed disabled:opacity-50"
      >
        <span className="truncate">{selectedModelOption?.label ?? selectedModel?.model ?? "Mặc định"}</span>
        <ChevronDownIcon
          className={`h-3.5 w-3.5 shrink-0 text-secondary-text transition ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open ? (
        <div
          role="menu"
          aria-label="Chọn model ưu tiên"
          className="absolute bottom-full left-0 z-30 mb-2 w-[min(320px,calc(100vw-2rem))] overflow-hidden rounded-lg border border-warm-border bg-surface-card shadow-xl"
        >
          <div className="px-3 pb-1 pt-3 text-[11px] font-semibold uppercase text-secondary-text">
            Mô hình
          </div>
          <div className="max-h-52 overflow-y-auto px-1.5 pb-2">
            {models.length ? (
              models.map((option) => (
                <button
                  key={`${option.provider}:${option.model}`}
                  type="button"
                  role="menuitemradio"
                  disabled={!option.available}
                  aria-checked={isSelectedModel(option, selectedModel)}
                  onClick={() => {
                    onSelectModel({ provider: option.provider, model: option.model });
                    setOpen(false);
                  }}
                  className="grid w-full grid-cols-[20px_1fr] gap-2 rounded-md px-2 py-2.5 text-left transition hover:bg-main-background disabled:cursor-not-allowed disabled:opacity-45"
                >
                  <Checkmark visible={isSelectedModel(option, selectedModel)} />
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium text-primary-text">
                      {option.label}
                    </span>
                    <span className="mt-0.5 block truncate text-xs text-secondary-text">
                      {option.description} · {option.available ? "Sẵn sàng" : "Chưa cấu hình API key"}
                    </span>
                  </span>
                </button>
              ))
            ) : (
              <p className="px-2 py-3 text-xs text-secondary-text">Backend chưa có provider khả dụng.</p>
            )}
          </div>
          <div className="border-t border-warm-border px-3 py-2.5 text-xs text-secondary-text">
            {availableModelCount > 1
              ? "Tự chuyển sang model còn lại nếu model ưu tiên lỗi."
              : "Cấu hình cả hai API key để bật fallback tự động."}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function SkillCommandMenu({
  skills,
  query,
  selectedSkillName,
  onSelect,
}: {
  skills: ChatSkillOption[];
  query: string | null;
  selectedSkillName: string | null;
  onSelect: (skill: ChatSkillOption) => void;
}) {
  if (query === null) return null;
  const normalizedQuery = query.trim().toLowerCase();
  const matches = skills.filter(
    (skill) =>
      skill.name.toLowerCase().includes(normalizedQuery) ||
      skill.description.toLowerCase().includes(normalizedQuery),
  );

  return (
    <div
      role="listbox"
      aria-label="Danh sách skill"
      className="absolute bottom-full left-3 z-20 mb-2 w-[min(380px,calc(100vw-3rem))] rounded-lg border border-warm-border bg-surface-card p-1.5 shadow-xl"
    >
      <div className="px-2 pb-1 pt-1 text-[11px] font-semibold uppercase text-secondary-text">
        Skills
      </div>
      <div className="max-h-56 overflow-y-auto">
        {matches.length ? (
          matches.map((skill) => (
            <button
              key={skill.name}
              type="button"
              role="option"
              aria-selected={selectedSkillName === skill.name}
              onClick={() => onSelect(skill)}
              className="block w-full rounded-md px-2 py-2 text-left transition hover:bg-main-background"
            >
              <span className="block text-sm font-medium">/{skill.name}</span>
              <span className="mt-0.5 block line-clamp-2 text-xs leading-5 text-secondary-text">
                {skill.description}
              </span>
            </button>
          ))
        ) : (
          <p className="px-2 py-3 text-xs text-secondary-text">Không có skill phù hợp.</p>
        )}
      </div>
    </div>
  );
}
