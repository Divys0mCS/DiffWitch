import type {
  RunDetail,
  RunsResponse,
  SettingsResponse,
  UsageResponse,
} from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000").replace(
  /\/+$/,
  "",
);

export const TOKEN_KEY = "diffwitch.token";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

/** The Render free instance sleeps; this error drives the "waking up" UI. */
export class ColdStartError extends Error {
  constructor() {
    super("The review backend is waking up.");
    this.name = "ColdStartError";
  }
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  token?: string | null;
  timeoutMs?: number;
  signal?: AbortSignal;
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, token, timeoutMs = 12000, signal } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  if (signal) {
    signal.addEventListener("abort", () => controller.abort(), { once: true });
  }

  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const bearer = token ?? getToken();
  if (bearer) headers.Authorization = `Bearer ${bearer}`;

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      cache: "no-store",
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ColdStartError();
    }
    throw new ColdStartError();
  } finally {
    clearTimeout(timer);
  }

  if (response.status === 401) {
    throw new ApiError("Your session is not valid. Sign in again.", 401);
  }
  if (response.status === 503) {
    const detail = await safeDetail(response);
    throw new ApiError(detail ?? "The backend is not ready yet.", 503);
  }
  if (!response.ok) {
    const detail = await safeDetail(response);
    throw new ApiError(detail ?? `Request failed (${response.status})`, response.status);
  }
  return (await response.json()) as T;
}

async function safeDetail(response: Response): Promise<string | null> {
  try {
    const payload = (await response.json()) as { detail?: string };
    return typeof payload.detail === "string" ? payload.detail : null;
  } catch {
    return null;
  }
}

export const api = {
  login: (password: string) =>
    apiFetch<{ token: string; expires_in: number }>("/api/auth/login", {
      method: "POST",
      body: { password },
      timeoutMs: 20000,
    }),
  me: () => apiFetch<{ authenticated: boolean; version: string }>("/api/auth/me"),
  runs: (query = "") => apiFetch<RunsResponse>(`/api/runs${query}`),
  run: (id: string) => apiFetch<RunDetail>(`/api/runs/${id}`),
  rerun: (id: string) =>
    apiFetch<{ status: string; run_id: string }>(`/api/runs/${id}/rerun`, {
      method: "POST",
      timeoutMs: 20000,
    }),
  usage: () => apiFetch<UsageResponse>("/api/usage"),
  settings: () => apiFetch<SettingsResponse>("/api/settings"),
  saveSettings: (payload: Record<string, unknown>) =>
    apiFetch<SettingsResponse>("/api/settings", { method: "PUT", body: payload }),
};
