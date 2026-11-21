"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { isUnauthorized, useAuth } from "@/components/auth-provider";
import { RunRow } from "@/components/run-row";
import { WakeBanner } from "@/components/wake-banner";
import { api, ApiError, ColdStartError } from "@/lib/api";
import type { Run } from "@/lib/types";

const FILTERS = ["all", "queued", "running", "completed", "failed", "skipped"] as const;
type Filter = (typeof FILTERS)[number];

export default function RunsPage() {
  const { logout } = useAuth();
  const router = useRouter();
  const [runs, setRuns] = useState<Run[]>([]);
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useState<Filter>("all");
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<"connecting" | "live" | "polling" | "waking" | "offline" | "done">(
    "connecting",
  );
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (nextFilter: Filter = filter) => {
      try {
        const query = nextFilter === "all" ? "" : `?status=${nextFilter}`;
        const data = await api.runs(query);
        setRuns(data.runs);
        setTotal(data.total);
        setMode("live");
        setError(null);
      } catch (caught) {
        if (isUnauthorized(caught)) {
          logout();
          router.replace("/login");
          return;
        }
        if (caught instanceof ColdStartError) {
          setMode("waking");
        } else if (caught instanceof ApiError) {
          setError(caught.message);
          setMode("offline");
        } else {
          setError("Could not load runs.");
          setMode("offline");
        }
      } finally {
        setLoading(false);
      }
    },
    [filter, logout, router],
  );

  useEffect(() => {
    setLoading(true);
    void load(filter);
  }, [filter, load]);

  useEffect(() => {
    if (mode !== "waking") return;
    const timer = setTimeout(() => void load(filter), 3000);
    return () => clearTimeout(timer);
  }, [mode, load, filter]);

  const hasActive = runs.some((run) => run.status === "queued" || run.status === "running");
  useEffect(() => {
    if (!hasActive) return;
    const timer = setInterval(() => void load(filter), 5000);
    return () => clearInterval(timer);
  }, [hasActive, load, filter]);

  const completed = runs.filter((run) => run.status === "completed").length;

  return (
    <AppShell>
      <div className="space-y-6">
        <header className="animate-rise">
          <p className="font-mono text-[10px] tracking-[0.28em] text-violet/70 uppercase">
            The ledger
          </p>
          <div className="mt-2 flex flex-wrap items-end justify-between gap-4">
            <div>
              <h1 className="font-display text-3xl tracking-tight text-frost sm:text-4xl">
                Review runs
              </h1>
              <p className="mt-2 max-w-xl text-sm leading-relaxed text-mist">
                Every pull request the agent has reasoned about, with the trace it left behind.
              </p>
            </div>
            <button
              onClick={() => void load(filter)}
              className="rounded-xl border border-line bg-white/[0.02] px-3.5 py-2 text-xs text-mist transition-colors hover:border-violet/40 hover:text-violet"
            >
              Refresh
            </button>
          </div>
        </header>

        <WakeBanner mode={mode} error={error} onRetry={() => void load(filter)} />

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap gap-1.5">
            {FILTERS.map((item) => (
              <button
                key={item}
                onClick={() => setFilter(item)}
                className={`rounded-lg border px-3 py-1.5 font-mono text-[11px] tracking-wide transition-all ${
                  filter === item
                    ? "border-violet/40 bg-violet/10 text-violet"
                    : "border-line/70 text-mist/70 hover:border-line hover:text-frost"
                }`}
              >
                {item}
              </button>
            ))}
          </div>
          <p className="font-mono text-[11px] text-mist/50">
            {total} run{total === 1 ? "" : "s"} · {completed} reviewed
          </p>
        </div>

        <section className="panel overflow-hidden">
          {loading && runs.length === 0 ? (
            <div className="space-y-3 p-5">
              {[0, 1, 2].map((row) => (
                <div key={row} className="shimmer h-16 rounded-xl border border-line/60 bg-white/[0.02]" />
              ))}
            </div>
          ) : runs.length === 0 ? (
            <div className="flex flex-col items-center gap-3 px-6 py-16 text-center">
              <span className="text-2xl text-violet/60">◇</span>
              <p className="font-display text-lg text-frost">No runs yet</p>
              <p className="max-w-md text-sm leading-relaxed text-mist">
                Install the GitHub App on a repository and open a pull request. DiffWitch will
                appear here within a few seconds of the webhook.
              </p>
            </div>
          ) : (
            runs.map((run, index) => <RunRow key={run.id} run={run} index={index} />)
          )}
        </section>
      </div>
    </AppShell>
  );
}
