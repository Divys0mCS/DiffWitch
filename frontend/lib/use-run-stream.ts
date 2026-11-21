"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL, api, ColdStartError, getToken } from "./api";
import type { Run, Step, StreamEvent } from "./types";

export type StreamMode = "connecting" | "live" | "polling" | "waking" | "offline" | "done";

const TERMINAL = new Set(["completed", "failed", "skipped"]);

interface StreamState {
  run: Run | null;
  steps: Step[];
  mode: StreamMode;
  error: string | null;
  refresh: () => void;
}

/**
 * Live run feed.
 *
 * 1. Opens an SSE connection straight to Render (never through Vercel).
 * 2. If the stream fails, falls back to polling every 2 seconds.
 * 3. If the free instance is asleep, reports "waking" and retries automatically.
 */
export function useRunStream(runId: string, initial?: { run: Run; steps: Step[] }): StreamState {
  const [run, setRun] = useState<Run | null>(initial?.run ?? null);
  const [steps, setSteps] = useState<Step[]>(initial?.steps ?? []);
  const [mode, setMode] = useState<StreamMode>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const mergeStep = useCallback((incoming: Step) => {
    setSteps((previous) => {
      const index = previous.findIndex((step) => step.seq === incoming.seq);
      if (index === -1) return [...previous, incoming].sort((a, b) => a.seq - b.seq);
      const next = [...previous];
      next[index] = incoming;
      return next;
    });
  }, []);

  useEffect(() => {
    let disposed = false;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let wakeTimer: ReturnType<typeof setTimeout> | null = null;
    const controller = new AbortController();

    const stopTimers = () => {
      if (pollTimer) clearInterval(pollTimer);
      if (wakeTimer) clearTimeout(wakeTimer);
      pollTimer = null;
      wakeTimer = null;
    };

    const applySnapshot = (nextRun: Run, nextSteps: Step[]) => {
      setRun(nextRun);
      setSteps(nextSteps);
      if (TERMINAL.has(nextRun.status)) setMode("done");
    };

    const poll = async () => {
      try {
        const detail = await api.run(runId);
        if (disposed) return;
        applySnapshot(detail.run, detail.steps);
        if (TERMINAL.has(detail.run.status) && pollTimer) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
        if (mode === "waking") setMode("polling");
      } catch (caught) {
        if (disposed) return;
        if (caught instanceof ColdStartError) {
          setMode("waking");
        } else {
          setError(caught instanceof Error ? caught.message : "Could not load this run.");
          setMode("offline");
        }
      }
    };

    const startPolling = () => {
      if (disposed || pollTimer) return;
      setMode((current) => (current === "waking" ? current : "polling"));
      void poll();
      pollTimer = setInterval(() => void poll(), 2000);
    };

    const handleEvent = (event: StreamEvent) => {
      if (disposed) return;
      if (event.type === "snapshot") {
        applySnapshot(event.run, event.steps);
        return;
      }
      if (event.type === "step") {
        mergeStep({
          seq: event.seq,
          kind: event.kind,
          title: event.title,
          detail: event.detail,
          tokens: event.tokens,
          duration_ms: event.duration_ms,
          at: event.at,
        });
        return;
      }
      if (event.type === "run") {
        setRun((current) => (current ? { ...current, status: event.status } : current));
        if (event.final) {
          setMode("done");
          void poll();
        }
        return;
      }
      if (event.type === "error") setError(event.message);
    };

    const startStream = async () => {
      const token = getToken();
      try {
        const response = await fetch(`${API_URL}/api/runs/${runId}/events`, {
          headers: token ? { Authorization: `Bearer ${token}`, Accept: "text/event-stream" } : {},
          signal: controller.signal,
          cache: "no-store",
        });
        if (!response.ok || !response.body) throw new Error("stream unavailable");
        setMode("live");

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done || disposed) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";
          for (const frame of frames) {
            const dataLine = frame
              .split("\n")
              .find((line) => line.startsWith("data:"));
            if (!dataLine) continue;
            try {
              handleEvent(JSON.parse(dataLine.slice(5).trim()) as StreamEvent);
            } catch {
              /* ignore malformed frame */
            }
          }
        }
        if (!disposed) startPolling();
      } catch {
        if (disposed) return;
        const token2 = getToken();
        if (!token2) {
          setMode("offline");
          setError("Sign in again to stream this run.");
          return;
        }
        setMode("waking");
        wakeTimer = setTimeout(() => {
          if (!disposed) startPolling();
        }, 2500);
      }
    };

    void startStream();

    return () => {
      disposed = true;
      controller.abort();
      stopTimers();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, nonce, mergeStep]);

  const refresh = useCallback(() => {
    setError(null);
    setMode("connecting");
    setNonce((value) => value + 1);
  }, []);

  return { run, steps, mode, error, refresh };
}
