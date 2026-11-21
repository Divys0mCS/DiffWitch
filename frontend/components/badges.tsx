import type { Risk, RunStatus } from "@/lib/types";

const STATUS_STYLES: Record<string, { label: string; classes: string; dot: string }> = {
  queued: {
    label: "Queued",
    classes: "border-line/80 bg-white/[0.03] text-mist",
    dot: "bg-mist/70",
  },
  running: {
    label: "Running",
    classes: "border-violet/40 bg-violet/10 text-violet",
    dot: "bg-violet animate-breathe",
  },
  completed: {
    label: "Reviewed",
    classes: "border-mint/35 bg-mint/[0.08] text-mint",
    dot: "bg-mint",
  },
  failed: {
    label: "Failed",
    classes: "border-blood/40 bg-blood/10 text-blood",
    dot: "bg-blood",
  },
  skipped: {
    label: "Skipped",
    classes: "border-ember/35 bg-ember/[0.08] text-ember",
    dot: "bg-ember",
  },
};

export function StatusBadge({ status }: { status: RunStatus | string }) {
  const style = STATUS_STYLES[status] ?? STATUS_STYLES.queued;
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-[11px] font-medium tracking-wide uppercase ${style.classes}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
      {style.label}
    </span>
  );
}

const RISK_STYLES: Record<string, { label: string; classes: string }> = {
  low: { label: "Low risk", classes: "border-mint/35 bg-mint/[0.08] text-mint" },
  medium: { label: "Medium risk", classes: "border-ember/40 bg-ember/10 text-ember" },
  high: { label: "High risk", classes: "border-blood/45 bg-blood/10 text-blood" },
};

export function RiskBadge({ risk }: { risk: Risk }) {
  if (!risk) return <span className="text-xs text-mist/60">—</span>;
  const style = RISK_STYLES[risk] ?? RISK_STYLES.low;
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium tracking-wide ${style.classes}`}
    >
      {style.label}
    </span>
  );
}

export function ModeBadge({ mode }: { mode: string }) {
  return (
    <span className="inline-flex items-center rounded-md border border-line/80 bg-white/[0.02] px-2 py-0.5 font-mono text-[10px] tracking-[0.14em] text-mist uppercase">
      {mode}
    </span>
  );
}

export function SeverityDot({ severity }: { severity: string }) {
  const tone =
    severity === "critical" ? "bg-blood" : severity === "warning" ? "bg-ember" : "bg-mist";
  return <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${tone}`} />;
}
