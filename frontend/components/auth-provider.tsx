"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, ApiError, setToken as persistToken, getToken } from "@/lib/api";

interface AuthState {
  token: string | null;
  ready: boolean;
  login: (password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setTokenState] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setTokenState(getToken());
    setReady(true);
  }, []);

  const login = useCallback(async (password: string) => {
    const result = await api.login(password);
    persistToken(result.token);
    setTokenState(result.token);
  }, []);

  const logout = useCallback(() => {
    persistToken(null);
    setTokenState(null);
  }, []);

  const value = useMemo(
    () => ({ token, ready, login, logout }),
    [token, ready, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}

export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}
