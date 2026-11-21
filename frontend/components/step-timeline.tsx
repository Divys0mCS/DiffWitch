"use client";

import { useState } from "react";
import { clockTime } from "@/lib/format";
import type { Step } from "@/lib/types";

const KIND_META: Record<
  string,
  { glyph: string; label: string; tone: string; node: string }
> = {
  llm_call: {
    glyph: "◆",
    label: "Model",
    tone: "text-violet",
    node: "border-violet/45 bg-violet/10 text-violet",
  },
  tool_call: {
    glyph: "→",
    label: "Tool call",
    tone: "text-ember",
    node: "border-ember/45 bg-ember/10 text-ember",
  },
  tool_result: {
    glyph: "←",
    label: "Tool result",
    tone: "text-mint",
    node: "border-mint/45 bg-mint/10 text-mint",
  },
  note: {
    glyph: "·",
    label: "Note",
    tone: "text-mist",
    node: "border-line bg-white/[0.04] text-mist",
  },
  verdict: {
    glyph: "★",
    label: "Verdict",
    tone: "text-mint",
    node: "border-mint/50 bg-mint/10 text-mint",
  },
  error: {
    glyph: "!",
    label: "Error",
    tone: "text-blood",
    node: "border-blood/50 bg-blood/10 text-blood",
  },
};

function detailPreview(detail: Record<string, unknown> | null): string | null {
  if (!detail) return null;
  const keys = ["message", "summary", "thought", "preview", "reason", "detail"];
  for (const key of keys) {
    const value = detail[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

function StepRow({ step, index }: { step: Step; index: number }) {
  const [open, setOpen] = useState(false);
  const meta = KIND_META[step.kind] ?? KIND_META.note;
  const preview = detailPreview(step.detail);

  return (
    <li
      className="relative animate-rise pl-12"
      style={{ animationDelay: `${Math.min(index, 12) * 55}ms` }}
    >
      <span className="absolute left-[13px] top-0 h-full w-px timeline-rail" aria-hidden />
      <span
        className={`absolute left-0 top-1 flex h-7 w-7 items-center justify-center rounded-full border text-[11px] ${meta.node}`}
        aria-hidden
      >
        {meta.glyph}
      </span>

      <div className="group rounded-xl border border-transparent px-3 py-2 transition-colors hover:border-line/80 hover:bg-white/[0.02]">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="text-sm text-frost">{step.title ?? meta.label}</span>
          <span className={`font-mono text-[10px] tracking-[0.14em] uppercase ${meta.tone}`}>
            {meta.label}
          </span>
          <span className="ml-auto flex items-center gap-2 font-mono text-[10px] text-mist/50">
            {step.tokens ? <span>{step.tokens} tok</span> : null}
            {step.duration_ms !== null && step.duration_ms !== undefined ? (
              <span>{step.duration_ms} ms</span>
            ) : null}
            {step.at ? <span>{clockTime(step.at)}</span> : null}
          </span>
        </div>

        {preview && !open && (
          <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-mist/80">{preview}</p>
        )}

        {step.detail && Object.keys(step.detail).length > 0 && (
          <>
            <button
              onClick={() => setOpen((value) => !value)}
              className="mt-1.5 font-mono text-[10px] tracking-wide text-mist/50 transition-colors hover:text-violet"
            >
              {open ? "hide detail" : "inspect detail"}
            </button>
            {open && (
              <pre className="animate-fade mt-2 max-h-72 overflow-auto rounded-lg border border-line/70 bg-ink/70 p-3 font-mono text-[11px] leading-relaxed text-mist">
                {JSON.stringify(step.detail, null, 2)}
              </pre>
            )}
          </>
        )}
      </div>
    </li>
  );
}

export function StepTimeline({
  steps,
  active,
  emptyLabel = "No steps yet.",
}: {
  steps: Step[];
  active: boolean;
  emptyLabel?: string;
}) {
  return (
    <div className="panel p-5 sm:p-6">
      <div className="mb-5 flex items-center justify-between">
        <h2 className="font-display text-sm tracking-[0.2em] text-mist uppercase">
          Reasoning trace
        </h2>
        <span className="font-mono text-[10px] text-mist/50">
          {steps.length} step{steps.length === 1 ? "" : "s"}
        </span>
      </div>

      {steps.length === 0 && !active && (
        <p className="py-8 text-center text-sm text-mist/60">{emptyLabel}</p>
      )}

      <ol className="space-y-1">
        {steps.map((step, index) => (
          <StepRow key={step.seq} step={step} index={index} />
        ))}
      </ol>

      {active && (
        <div className="mt-4 flex items-center gap-3 pl-1">
          <span className="relative flex h-7 w-7 items-center justify-center">
            <span className="absolute h-7 w-7 rounded-full border border-violet/30 animate-breathe" />
            <span className="h-1.5 w-1.5 rounded-full bg-violet animate-blink" />
          </span>
          <span className="font-mono text-[11px] tracking-[0.16em] text-violet/80 uppercase">
            agent thinking
          </span>
        </div>
      )}
    </div>
  );
}
