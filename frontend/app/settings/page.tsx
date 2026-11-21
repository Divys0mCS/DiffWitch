"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { isUnauthorized, useAuth } from "@/components/auth-provider";
import { WakeBanner } from "@/components/wake-banner";
import { api, ApiError, ColdStartError } from "@/lib/api";
import type { SettingsResponse } from "@/lib/types";

type Draft = {
  review_mode: "lite" | "agent";
  max_inline_comments: number;
  max_agent_steps: number;
  daily_llm_budget: number;
  max_files_per_review: number;
  ignore_globs: string;
};

function toDraft(settings: SettingsResponse): Draft {
  return {
    review_mode: settings.review_mode,
    max_inline_comments: settings.max_inline_comments,
    max_agent_steps: settings.max_agent_steps,
    daily_llm_budget: settings.daily_llm_budget,
    max_files_per_review: settings.max_files_per_review,
    ignore_globs: settings.ignore_globs.join(", "),
  };
}

function NumberField({
  label,
  hint,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  hint: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block rounded-xl border border-line/70 bg-white/[0.02] p-4">
      <span className="font-mono text-[10px] tracking-[0.16em] text-mist/60 uppercase">
        {label}
      </span>
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="mt-2 w-full rounded-lg border border-line bg-ink/60 px-3 py-2 font-mono text-sm text-frost outline-none transition-colors focus:border-violet/50"
      />
      <span className="mt-2 block text-[11px] leading-relaxed text-mist/50">{hint}</span>
    </label>
  );
}

export default function SettingsPage() {
  const { logout } = useAuth();
  const router = useRouter();
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [mode, setMode] = useState<"connecting" | "live" | "polling" | "waking" | "offline" | "done">(
    "connecting",
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.settings();
      setSettings(data);
      setDraft(toDraft(data));
      setMode("live");
      setError(null);
    } catch (caught) {
      if (isUnauthorized(caught)) {
        logout();
        router.replace("/login");
        return;
      }
      if (caught instanceof ColdStartError) setMode("waking");
      else {
        setMode("offline");
        setError(caught instanceof ApiError ? caught.message : "Could not load settings.");
      }
    }
  }, [logout, router]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (mode !== "waking") return;
    const timer = setTimeout(() => void load(), 3000);
    return () => clearTimeout(timer);
  }, [mode, load]);

  const save = async () => {
    if (!draft || saving) return;
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const updated = await api.saveSettings({
        review_mode: draft.review_mode,
        max_inline_comments: draft.max_inline_comments,
        max_agent_steps: draft.max_agent_steps,
        daily_llm_budget: draft.daily_llm_budget,
        max_files_per_review: draft.max_files_per_review,
        ignore_globs: draft.ignore_globs
          .split(/[,\n]/)
          .map((item) => item.trim())
          .filter(Boolean),
      });
      setSettings(updated);
      setDraft(toDraft(updated));
      setSaved("Settings saved. New runs use them immediately.");
    } catch (caught) {
      if (isUnauthorized(caught)) {
        logout();
        router.replace("/login");
        return;
      }
      setError(caught instanceof ApiError ? caught.message : "Could not save settings.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <AppShell>
      <div className="space-y-6">
        <header className="animate-rise">
          <p className="font-mono text-[10px] tracking-[0.28em] text-violet/70 uppercase">
            The familiar
          </p>
          <h1 className="mt-2 font-display text-3xl tracking-tight text-frost sm:text-4xl">
            Settings
          </h1>
          <p className="mt-2 max-w-xl text-sm leading-relaxed text-mist">
            How the agent thinks. These override the backend environment defaults for every new
            run.
          </p>
        </header>

        <WakeBanner mode={mode} error={error} onRetry={() => void load()} />

        {!draft ? (
          <div className="panel shimmer h-72" />
        ) : (
          <>
            <section className="panel animate-rise p-6 sm:p-7">
              <h2 className="font-display text-sm tracking-[0.2em] text-mist uppercase">
                Review mode
              </h2>
              <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
                {(
                  [
                    {
                      value: "lite" as const,
                      title: "LITE",
                      blurb:
                        "One model call per pull request. All filtered files batched together. Fits the free tier.",
                    },
                    {
                      value: "agent" as const,
                      title: "AGENT",
                      blurb:
                        "A bounded tool loop that can read surrounding files before deciding. Costs more calls.",
                    },
                  ]
                ).map((option) => {
                  const active = draft.review_mode === option.value;
                  return (
                    <button
                      key={option.value}
                      onClick={() => setDraft({ ...draft, review_mode: option.value })}
                      className={`rounded-xl border p-4 text-left transition-all ${
                        active
                          ? "border-violet/45 bg-violet/[0.09] ring-glow"
                          : "border-line/70 bg-white/[0.02] hover:border-line"
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span
                          className={`font-display text-sm tracking-[0.18em] ${active ? "text-violet" : "text-frost"}`}
                        >
                          {option.title}
                        </span>
                        <span
                          className={`h-3 w-3 rounded-full border ${
                            active ? "border-violet bg-violet" : "border-mist/40"
                          }`}
                        />
                      </div>
                      <p className="mt-2 text-[12px] leading-relaxed text-mist/70">
                        {option.blurb}
                      </p>
                    </button>
                  );
                })}
              </div>
            </section>

            <section
              className="panel animate-rise p-6 sm:p-7"
              style={{ animationDelay: "70ms" }}
            >
              <h2 className="font-display text-sm tracking-[0.2em] text-mist uppercase">
                Limits
              </h2>
              <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
                <NumberField
                  label="Daily LLM budget"
                  hint="Calls per UTC day. 0 means unlimited. Keep below your AI Studio RPD."
                  value={draft.daily_llm_budget}
                  min={0}
                  max={10000}
                  onChange={(value) => setDraft({ ...draft, daily_llm_budget: value })}
                />
                <NumberField
                  label="Max inline comments"
                  hint="Hard cap on comments per review."
                  value={draft.max_inline_comments}
                  min={1}
                  max={100}
                  onChange={(value) => setDraft({ ...draft, max_inline_comments: value })}
                />
                <NumberField
                  label="Max agent steps"
                  hint="Total model calls allowed in AGENT mode, including the final answer."
                  value={draft.max_agent_steps}
                  min={1}
                  max={10}
                  onChange={(value) => setDraft({ ...draft, max_agent_steps: value })}
                />
                <NumberField
                  label="Max files per review"
                  hint="Additional changed files are omitted from the prompt, largest first."
                  value={draft.max_files_per_review}
                  min={1}
                  max={200}
                  onChange={(value) => setDraft({ ...draft, max_files_per_review: value })}
                />
              </div>
            </section>

            <section
              className="panel animate-rise p-6 sm:p-7"
              style={{ animationDelay: "120ms" }}
            >
              <h2 className="font-display text-sm tracking-[0.2em] text-mist uppercase">
                Filters
              </h2>
              <p className="mt-2 text-[12px] leading-relaxed text-mist/60">
                Comma separated globs, matched against the file path. Lockfiles, binaries and
                vendored folders are always skipped.
              </p>
              <textarea
                value={draft.ignore_globs}
                onChange={(event) => setDraft({ ...draft, ignore_globs: event.target.value })}
                rows={4}
                spellCheck={false}
                className="mt-3 w-full rounded-xl border border-line bg-ink/60 px-3.5 py-3 font-mono text-[12px] leading-relaxed text-frost outline-none transition-colors focus:border-violet/50"
              />
            </section>

            <div className="flex flex-wrap items-center gap-3">
              <button
                onClick={() => void save()}
                disabled={saving}
                className="rounded-xl border border-violet/40 bg-violet/[0.14] px-5 py-2.5 text-sm text-frost transition-colors hover:bg-violet/[0.2] disabled:opacity-50"
              >
                {saving ? "Saving…" : "Save settings"}
              </button>
              {settings && (
                <button
                  onClick={() => setDraft(toDraft({ ...settings, ...settings.defaults }))}
                  className="rounded-xl border border-line px-4 py-2.5 text-sm text-mist transition-colors hover:border-line hover:text-frost"
                >
                  Reset to defaults
                </button>
              )}
              {saved && <span className="text-xs text-mint">{saved}</span>}
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}
