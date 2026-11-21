import Link from "next/link";
import { relativeTime, shortSha } from "@/lib/format";
import type { Run } from "@/lib/types";
import { ModeBadge, RiskBadge, StatusBadge } from "./badges";

export function RunRow({ run, index }: { run: Run; index: number }) {
  return (
    <Link
      href={`/runs/${run.id}`}
      className="group relative block animate-rise border-b border-line/50 px-4 py-4 transition-colors last:border-b-0 hover:bg-white/[0.025] sm:px-5"
      style={{ animationDelay: `${Math.min(index, 14) * 40}ms` }}
    >
      <span className="pointer-events-none absolute inset-y-0 left-0 w-px bg-transparent transition-colors group-hover:bg-violet/60" />
      <div className="flex flex-wrap items-start gap-x-4 gap-y-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
            <span className="font-mono text-[13px] text-frost/90">{run.repository}</span>
            <span className="font-mono text-[13px] text-violet">#{run.pr_number}</span>
            <ModeBadge mode={run.mode} />
            {run.skip_reason ? (
              <span className="font-mono text-[10px] tracking-wide text-ember/80">
                {run.skip_reason}
              </span>
            ) : null}
          </div>
          <p className="mt-1 line-clamp-1 text-sm text-mist/90">
            {run.pr_title || "Untitled pull request"}
          </p>
          <p className="mt-1 font-mono text-[10px] text-mist/40">
            {shortSha(run.head_sha)} · {relativeTime(run.created_at)}
            {run.comments_posted > 0 ? ` · ${run.comments_posted} comment(s)` : ""}
            {run.llm_calls > 0 ? ` · ${run.llm_calls} llm call(s)` : ""}
          </p>
        </div>

        <div className="flex items-center gap-3 sm:gap-4">
          <RiskBadge risk={run.risk} />
          <StatusBadge status={run.status} />
          <span className="hidden text-mist/30 transition-transform group-hover:translate-x-0.5 group-hover:text-violet sm:block">
            →
          </span>
        </div>
      </div>
    </Link>
  );
}
