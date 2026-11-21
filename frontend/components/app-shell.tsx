"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { useAuth } from "./auth-provider";
import { Sigil } from "./sigil";

const NAV = [
  { href: "/", label: "Runs", glyph: "◇" },
  { href: "/usage", label: "Quota", glyph: "◈" },
  { href: "/settings", label: "Settings", glyph: "✧" },
];

function Screen({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-dvh items-center justify-center px-6">
      <div className="flex flex-col items-center gap-4 text-center">
        <Sigil size={52} />
        {children}
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { token, ready, logout } = useAuth();
  const isPublic = pathname === "/login";

  useEffect(() => {
    if (!ready) return;
    if (!token && !isPublic) router.replace("/login");
    if (token && isPublic) router.replace("/");
  }, [ready, token, isPublic, router]);

  if (isPublic) return <>{children}</>;

  if (!ready) {
    return (
      <Screen>
        <p className="text-sm text-mist">Opening the grimoire…</p>
      </Screen>
    );
  }

  if (!token) {
    return (
      <Screen>
        <p className="text-sm text-mist">Redirecting to sign in…</p>
      </Screen>
    );
  }

  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-[1400px] gap-0 lg:gap-8 lg:px-8">
      <aside className="sticky top-0 hidden h-dvh w-[232px] shrink-0 flex-col justify-between py-8 lg:flex">
        <div>
          <Link href="/" className="group flex items-center gap-3">
            <Sigil size={40} />
            <span className="flex flex-col leading-none">
              <span className="font-display text-lg tracking-[0.18em] text-frost">DIFFWITCH</span>
              <span className="mt-1 font-mono text-[10px] tracking-[0.22em] text-mist/60">
                PR REVIEW AGENT
              </span>
            </span>
          </Link>

          <nav className="mt-12 flex flex-col gap-1.5">
            {NAV.map((item) => {
              const active =
                item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`group flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-all ${
                    active
                      ? "border border-violet/25 bg-violet/[0.08] text-frost ring-glow"
                      : "border border-transparent text-mist hover:border-line hover:bg-white/[0.02] hover:text-frost"
                  }`}
                >
                  <span
                    className={`text-xs ${active ? "text-violet" : "text-mist/60 group-hover:text-violet"}`}
                  >
                    {item.glyph}
                  </span>
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </div>

        <div className="space-y-4">
          <div className="rounded-xl border border-line/70 bg-white/[0.02] p-3">
            <p className="font-mono text-[10px] tracking-[0.16em] text-mist/60 uppercase">
              Free tier
            </p>
            <p className="mt-1.5 text-xs leading-relaxed text-mist">
              One Render service, Neon Postgres, Gemini free tier. The API sleeps after 15
              minutes of quiet.
            </p>
          </div>
          <button
            onClick={() => {
              logout();
              router.replace("/login");
            }}
            className="w-full rounded-xl border border-line px-3 py-2 text-xs text-mist transition-colors hover:border-blood/40 hover:text-blood"
          >
            Sign out
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 -mx-4 mb-2 flex items-center justify-between border-b border-line/60 bg-ink/80 px-4 py-3 backdrop-blur-xl lg:hidden">
          <Link href="/" className="flex items-center gap-2.5">
            <Sigil size={28} />
            <span className="font-display text-sm tracking-[0.2em]">DIFFWITCH</span>
          </Link>
          <nav className="flex items-center gap-1">
            {NAV.map((item) => {
              const active =
                item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`rounded-lg px-2.5 py-1.5 text-xs ${
                    active ? "bg-violet/15 text-violet" : "text-mist"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </header>

        <main className="min-w-0 flex-1 px-4 pb-16 pt-6 lg:px-0 lg:pt-10">{children}</main>
      </div>
    </div>
  );
}
