"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { isUnauthorized, useAuth } from "@/components/auth-provider";
import { WakeBanner } from "@/components/wake-banner";
import { api, ApiError, ColdStartError } from "@/lib/api";
import { compactNumber } from "@/lib/format";
import type { UsageResponse } from "@/lib/types";

function BudgetRing({ used, budget }: { used: number; budget: number | null }) {
  const ratio = budget && budget > 0 ? Math.min(1, used / budget) : 0;
  const radius = 54;
  const circumference = 2 * Math.PI * radius;
  return (
    <div className="relative h-36 w-36">
      <svg viewBox="0 0 128 128" className="h-36 w-36 -rotate-90">
        <circle cx="64" cy="64" r={radius} fill="none" stroke="#1e1e2e" strokeWidth="7" />
        <circle
          cx="64"
          cy="64"
          r={radius}
          fill="none"
          stroke="url(#budget-grad)"
          strokeWidth="7"
          strokeLinecap="round"
          strokeDasharray={`${circumference * ratio} ${circumference}`}
          className="transition-[stroke-dasharray] duration-700"
        />
        <defs>
          <linearGradient id="budget-grad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#a78bfa" />
            <stop offset="100%" stopColor="#f0b429" />
          </linearGradient>
        </defs>
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="font-display text-2xl text-frost">{used}</span>
        <span className="font-mono text-[10px] tracking-[0.16em] text-mist/60 uppercase">
          {budget ? `of ${budget}` : "unlimited"}
        </span>
      </div>
    </div>
  );
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded-xl border border-line/70 bg-white/[0.02] p-4">
      <p className="font-mono text-[10px] tracking-[0.16em] text-mist/50 uppercase">{label}</p>
      <p className="mt-1.5 font-display text-xl text-frost">{value}</p>
      {note && <p className="mt-1 text-[11px] leading-relaxed text-mist/60">{note}</p>}
    </div>
  );
}

export default function UsagePage() {
  const { logout } = useAuth();
  const router = useRouter();
  const [data, setData] = useState<UsageResponse | null>(null);
  const [mode, setMode] = useState<"connecting" | "live" | "polling" | "waking" | "offline" | "done">(
    "connecting",
  );
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.usage());
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
        setError(caught instanceof ApiError ? caught.message : "Could not load usage.");
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

  const maxDay = Math.max(1, ...(data?.days ?? []).map((day) => day.llm_calls));
  const totalTokens = (data?.totals.tokens_in ?? 0) + (data?.totals.tokens_out ?? 0);

  return (
    <AppShell>
      <div className="space-y-6">
        <header className="animate-rise">
          <p className="font-mono text-[10px] tracking-[0.28em] text-violet/70 uppercase">
            The ration
          </p>
          <h1 className="mt-2 font-display text-3xl tracking-tight text-frost sm:text-4xl">
            Quota &amp; spend
          </h1>
          <p className="mt-2 max-w-xl text-sm leading-relaxed text-mist">
            Gemini&apos;s free tier is small. DiffWitch counts every call in Postgres and stops
            before it runs out.
          </p>
        </header>

        <WakeBanner mode={mode} error={error} onRetry={() => void load()} />

        {!data ? (
          <div className="panel shimmer h-64" />
        ) : (
          <>
            <section className="panel animate-rise flex flex-col items-center gap-8 p-6 sm:flex-row sm:p-8">
              <BudgetRing used={data.today.llm_calls} budget={data.today.budget} />
              <div className="grid flex-1 grid-cols-1 gap-3 sm:grid-cols-2">
                <Stat
                  label="Remaining today"
                  value={
                    data.today.remaining === null ? "∞" : String(data.today.remaining)
                  }
                  note={`Resets at 00:00 UTC · ${data.today.day}`}
                />
                <Stat
                  label="Provider"
                  value={data.limits.provider}
                  note={
                    data.limits.model_configured
                      ? `model configured · ${data.limits.rpm} rpm / ${data.limits.rpd} rpd`
                      : "LLM_MODEL is not set yet"
                  }
                />
                <Stat
                  label="Tokens this session"
                  value={compactNumber(totalTokens)}
                  note={`${compactNumber(data.totals.tokens_in)} in · ${compactNumber(data.totals.tokens_out)} out`}
                />
                <Stat
                  label="Reviews all time"
                  value={String(data.totals.runs)}
                  note={`${data.totals.comments} inline comments posted`}
                />
              </div>
            </section>

            <section className="panel animate-rise p-6 sm:p-7" style={{ animationDelay: "80ms" }}>
              <div className="flex items-center justify-between">
                <h2 className="font-display text-sm tracking-[0.2em] text-mist uppercase">
                  Last 14 days
                </h2>
                <span className="font-mono text-[10px] text-mist/50">
                  budget {data.limits.daily_budget}/day
                </span>
              </div>

              {data.days.length === 0 ? (
                <p className="py-10 text-center text-sm text-mist/60">
                  No model calls recorded yet.
                </p>
              ) : (
                <div className="mt-6 flex h-40 items-end gap-1.5">
                  {[...data.days].reverse().map((day) => (
                    <div key={day.day} className="group flex flex-1 flex-col items-center gap-2">
                      <div className="relative flex w-full flex-1 items-end">
                        <div
                          className="w-full rounded-t-md bg-gradient-to-t from-violet/25 to-violet/70 transition-all group-hover:from-violet/40 group-hover:to-violet"
                          style={{ height: `${Math.max(4, (day.llm_calls / maxDay) * 100)}%` }}
                          title={`${day.llm_calls} calls on ${day.day}`}
                        />
                      </div>
                      <span className="font-mono text-[9px] text-mist/40">
                        {day.day.slice(5)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="panel animate-rise p-6 sm:p-7" style={{ animationDelay: "140ms" }}>
              <h2 className="font-display text-sm tracking-[0.2em] text-mist uppercase">
                Guard rails
              </h2>
              <dl className="mt-4 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
                {[
                  ["Review mode", data.limits.review_mode],
                  ["Daily call budget", String(data.limits.daily_budget)],
                  ["Per-minute limit", `${data.limits.rpm} rpm`],
                  ["Per-day limit", `${data.limits.rpd} rpd`],
                  ["Agent steps", String(data.limits.max_agent_steps)],
                  ["Runs today", String(data.today.runs)],
                ].map(([label, value]) => (
                  <div
                    key={label}
                    className="flex items-center justify-between rounded-lg border border-line/60 bg-white/[0.02] px-3.5 py-2.5"
                  >
                    <dt className="text-mist/70">{label}</dt>
                    <dd className="font-mono text-xs text-frost">{value}</dd>
                  </div>
                ))}
              </dl>
            </section>
          </>
        )}
      </div>
    </AppShell>
  );
}
