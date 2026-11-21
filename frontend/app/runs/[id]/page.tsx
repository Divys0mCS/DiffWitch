"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { AppShell } from "@/components/app-shell";
import { isUnauthorized, useAuth } from "@/components/auth-provider";
import { ModeBadge, RiskBadge, StatusBadge } from "@/components/badges";
import { StepTimeline } from "@/components/step-timeline";
import { WakeBanner } from "@/components/wake-banner";
import { api, ColdStartError } from "@/lib/api";
import { compactNumber, duration, relativeTime, shortSha } from "@/lib/format";
import { useRunStream } from "@/lib/use-run-stream";

export default function RunDetailPage() {
  const params = useParams<{ id: string }>();
  const runId = params.id;
  const router = useRouter();
  const { logout } = useAuth();
  const { run, steps, mode, error, refresh } = useRunStream(runId);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const active = run?.status === "queued" || run?.status === "running";

  const rerun = async () => {
    setBusy(true);
    setActionError(null);
    try {
      const result = await api.rerun(runId);
      router.push(`/runs/${result.run_id}`);
    } catch (caught) {
      if (isUnauthorized(caught)) {
        logout();
        router.replace("/login");
        return;
      }
      setActionError(
        caught instanceof ColdStartError
          ? "The backend is waking up. Try again in a moment."
          : "Could not queue a re-review.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppShell>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <Link
            href="/"
            className="font-mono text-[11px] tracking-wide text-mist/60 transition-colors hover:text-violet"
          >
            ← all runs
          </Link>
          <span className="font-mono text-[10px] text-mist/40">
            {mode === "live" ? (
              <span className="inline-flex items-center gap-1.5 text-mint/80">
                <span className="h-1.5 w-1.5 rounded-full bg-mint animate-breathe" />
                live
              </span>
            ) : mode === "polling" ? (
              "polling"
            ) : mode === "done" ? (
              "settled"
            ) : (
              ""
            )}
          </span>
        </div>

        <WakeBanner mode={mode} error={error} onRetry={refresh} />

        {!run ? (
          <div className="panel shimmer h-48" />
        ) : (
          <>
            <header className="panel animate-rise p-6 sm:p-8">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2.5">
                    <span className="font-mono text-sm text-frost">{run.repository}</span>
                    <span className="font-mono text-sm text-violet">#{run.pr_number}</span>
                    <ModeBadge mode={run.mode} />
                  </div>
                  <h1 className="mt-2 font-display text-2xl leading-snug text-frost sm:text-[28px]">
                    {run.pr_title || "Untitled pull request"}
                  </h1>
                  <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 font-mono text-[11px] text-mist/60">
                    <span>{shortSha(run.head_sha)}</span>
                    <span>{relativeTime(run.created_at)}</span>
                    <span>{duration(run.started_at, run.finished_at)}</span>
                    <span>{run.llm_calls} llm call(s)</span>
                    <span>
                      {compactNumber(run.tokens_in)} in / {compactNumber(run.tokens_out)} out
                    </span>
                  </div>
                </div>

                <div className="flex flex-col items-end gap-3">
                  <div className="flex items-center gap-2.5">
                    <RiskBadge risk={run.risk} />
                    <StatusBadge status={run.status} />
                  </div>
                  <div className="flex items-center gap-2">
                    {run.review_url && (
                      <a
                        href={run.review_url}
                        target="_blank"
                        rel="noreferrer"
                        className="rounded-lg border border-line px-3 py-1.5 text-xs text-mist transition-colors hover:border-mint/40 hover:text-mint"
                      >
                        View on GitHub ↗
                      </a>
                    )}
                    <button
                      onClick={() => void rerun()}
                      disabled={busy || active}
                      className="rounded-lg border border-violet/35 bg-violet/[0.1] px-3 py-1.5 text-xs text-violet transition-colors hover:bg-violet/[0.18] disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {busy ? "Queuing…" : "Re-review"}
                    </button>
                  </div>
                </div>
              </div>

              {actionError && (
                <p className="mt-4 rounded-lg border border-blood/35 bg-blood/[0.08] px-3 py-2 text-xs text-blood">
                  {actionError}
                </p>
              )}
            </header>

            {run.status === "completed" && run.summary && (
              <section className="panel animate-rise p-6 sm:p-7" style={{ animationDelay: "80ms" }}>
                <div className="flex items-center justify-between">
                  <h2 className="font-display text-sm tracking-[0.2em] text-mist uppercase">
                    Verdict
                  </h2>
                  <span className="font-mono text-[10px] text-mist/50">
                    {run.comments_posted} inline comment(s) · {run.files_reviewed} file(s)
                  </span>
                </div>
                <p className="mt-3 whitespace-pre-wrap text-[15px] leading-relaxed text-frost/90">
                  {run.summary}
                </p>
              </section>
            )}

            {run.status === "failed" && (
              <section className="animate-rise rounded-xl border border-blood/35 bg-blood/[0.06] p-5">
                <h2 className="font-mono text-[10px] tracking-[0.18em] text-blood/80 uppercase">
                  Run failed
                </h2>
                <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-blood/90">
                  {run.error || "No reason was recorded."}
                </p>
              </section>
            )}

            {run.status === "skipped" && (
              <section className="animate-rise rounded-xl border border-ember/30 bg-ember/[0.06] p-5">
                <h2 className="font-mono text-[10px] tracking-[0.18em] text-ember/80 uppercase">
                  Skipped · {run.skip_reason ?? "unknown"}
                </h2>
                <p className="mt-2 text-sm leading-relaxed text-ember/80">
                  {run.error ||
                    "This run was skipped before any model call. Re-review it from this page when you are ready."}
                </p>
              </section>
            )}

            <StepTimeline
              steps={steps}
              active={active}
              emptyLabel={
                run.status === "queued"
                  ? "Waiting for the worker to claim this run…"
                  : "No steps were recorded."
              }
            />
          </>
        )}
      </div>
    </AppShell>
  );
}
