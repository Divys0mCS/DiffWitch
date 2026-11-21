"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { isUnauthorized, useAuth } from "@/components/auth-provider";
import { Sigil } from "@/components/sigil";
import { ApiError, ColdStartError } from "@/lib/api";

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setError(null);
    try {
      await login(password);
      router.replace("/");
    } catch (caught) {
      if (caught instanceof ColdStartError) {
        setError("The backend is waking up. Give it up to a minute, then try again.");
      } else if (caught instanceof ApiError) {
        setError(
          caught.status === 503
            ? "The backend is not configured yet (DASHBOARD_PASSWORD is unset)."
            : caught.message,
        );
      } else if (isUnauthorized(caught)) {
        setError("That password was rejected.");
      } else {
        setError("Could not reach the backend.");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative flex min-h-dvh items-center justify-center px-5 py-12">
      <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
        <div className="absolute left-1/2 top-1/2 h-[36rem] w-[36rem] -translate-x-1/2 -translate-y-1/2 rounded-full bg-violet/[0.07] blur-3xl" />
      </div>

      <div className="panel animate-rise relative w-full max-w-md p-8 ring-glow sm:p-10">
        <div className="flex flex-col items-center text-center">
          <Sigil size={64} />
          <h1 className="mt-6 font-display text-2xl tracking-[0.2em] text-frost text-glow">
            DIFFWITCH
          </h1>
          <p className="mt-2 font-mono text-[10px] tracking-[0.24em] text-mist/60 uppercase">
            pull-request review agent
          </p>
          <p className="mt-6 text-sm leading-relaxed text-mist">
            Speak the passphrase to open the ledger.
          </p>
        </div>

        <form onSubmit={submit} className="mt-8 space-y-3">
          <label className="block">
            <span className="font-mono text-[10px] tracking-[0.18em] text-mist/60 uppercase">
              Dashboard password
            </span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              autoFocus
              placeholder="••••••••••••"
              className="mt-2 w-full rounded-xl border border-line bg-ink/60 px-4 py-3 font-mono text-sm text-frost outline-none transition-all placeholder:text-mist/30 focus:border-violet/50 focus:ring-2 focus:ring-violet/20"
            />
          </label>

          {error && (
            <p className="animate-fade rounded-lg border border-blood/35 bg-blood/[0.08] px-3 py-2 text-xs leading-relaxed text-blood">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy || !password}
            className="group relative w-full overflow-hidden rounded-xl border border-violet/40 bg-violet/[0.14] px-4 py-3 text-sm font-medium text-frost transition-all hover:bg-violet/[0.2] disabled:cursor-not-allowed disabled:opacity-50"
          >
            <span className="relative z-10">{busy ? "Unsealing…" : "Enter"}</span>
            <span className="absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-white/10 to-transparent transition-transform duration-700 group-hover:translate-x-full" />
          </button>
        </form>

        <p className="mt-6 text-center font-mono text-[10px] leading-relaxed text-mist/40">
          DASHBOARD_PASSWORD · sessions last 7 days
        </p>
      </div>
    </div>
  );
}
