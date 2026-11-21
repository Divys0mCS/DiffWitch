export type RunStatus = "queued" | "running" | "completed" | "failed" | "skipped";
export type Risk = "low" | "medium" | "high" | null;
export type StepKind =
  | "llm_call"
  | "tool_call"
  | "tool_result"
  | "note"
  | "verdict"
  | "error";

export interface Run {
  id: string;
  status: RunStatus;
  mode: "lite" | "agent";
  risk: Risk;
  pr_number: number;
  pr_title: string | null;
  head_sha: string;
  repository: string;
  summary: string | null;
  error: string | null;
  skip_reason: string | null;
  review_url: string | null;
  comments_posted: number;
  files_reviewed: number;
  llm_calls: number;
  tokens_in: number;
  tokens_out: number;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface Step {
  seq: number;
  kind: StepKind | string;
  title: string | null;
  detail: Record<string, unknown> | null;
  tokens: number | null;
  duration_ms: number | null;
  at: string | null;
}

export interface RunsResponse {
  runs: Run[];
  total: number;
}

export interface RunDetail {
  run: Run;
  steps: Step[];
}

export interface UsageDay {
  day: string;
  llm_calls: number;
  tokens_in: number;
  tokens_out: number;
  runs: number;
}

export interface UsageResponse {
  today: {
    day: string;
    llm_calls: number;
    budget: number | null;
    remaining: number | null;
    tokens_in: number;
    tokens_out: number;
    runs: number;
    rpm_limit: number;
  };
  limits: {
    rpm: number;
    rpd: number;
    daily_budget: number;
    review_mode: string;
    max_agent_steps: number;
    provider: string;
    model_configured: boolean;
  };
  days: UsageDay[];
  by_status: Record<string, number>;
  totals: { runs: number; comments: number; tokens_in: number; tokens_out: number };
}

export interface SettingsResponse {
  review_mode: "lite" | "agent";
  max_inline_comments: number;
  max_agent_steps: number;
  daily_llm_budget: number;
  max_files_per_review: number;
  ignore_globs: string[];
  defaults: {
    review_mode: "lite" | "agent";
    max_inline_comments: number;
    max_agent_steps: number;
    daily_llm_budget: number;
    max_files_per_review: number;
    ignore_globs: string[];
  };
}

export type StreamEvent =
  | { type: "snapshot"; run: Run; steps: Step[] }
  | { type: "step"; seq: number; kind: string; title: string; detail: Record<string, unknown> | null; tokens: number | null; duration_ms: number | null; at: string }
  | { type: "run"; status: RunStatus; final?: boolean; risk?: Risk; comments?: number }
  | { type: "error"; message: string };
