"use client";

interface Props {
  mode: "connecting" | "live" | "polling" | "waking" | "offline" | "done";
  error?: string | null;
  onRetry?: () => void;
}

export function WakeBanner({ mode, error, onRetry }: Props) {
  if (mode === "live" || mode === "done") return null;

  const waking = mode === "waking" || mode === "connecting";
  const message = waking
    ? "Waking up the server…"
    : mode === "polling"
      ? "Live stream unavailable — polling every 2 seconds."
      : error || "The backend is unreachable.";

  return (
    <div
      className={`animate-fade flex items-start gap-3 rounded-xl border px-4 py-3 text-sm ${
        mode === "offline"
          ? "border-blood/35 bg-blood/[0.07] text-blood"
          : "border-ember/30 bg-ember/[0.06] text-ember"
      }`}
    >
      <span className="relative mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center">
        <span className="absolute h-4 w-4 rounded-full bg-current opacity-20 animate-breathe" />
        <span className="h-1.5 w-1.5 rounded-full bg-current" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-medium">{message}</p>
        {waking && (
          <p className="mt-0.5 text-xs text-ember/70">
            Render&apos;s free instance sleeps after ~15 minutes idle and can take up to a minute
            to wake. This page retries on its own.
          </p>
        )}
      </div>
      {onRetry && (
        <button
          onClick={onRetry}
          className="shrink-0 rounded-lg border border-current px-2.5 py-1 text-xs opacity-70 transition-opacity hover:opacity-100"
        >
          Retry
        </button>
      )}
    </div>
  );
}
