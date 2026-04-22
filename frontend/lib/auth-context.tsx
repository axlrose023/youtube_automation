import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { getMe, login as apiLogin } from "@/lib/api";
import { clearTokens, getAccessToken, setTokens } from "@/lib/tokens";
import type { User } from "@/types/api";

type AuthContextValue = {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  refreshUser: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshUser = useCallback(async () => {
    const me = await getMe();
    setUser(me);
  }, []);

  useEffect(() => {
    if (!getAccessToken()) {
      setLoading(false);
      return;
    }

    getMe()
      .then(setUser)
      .catch(() => {
        clearTokens();
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const tokenPair = await apiLogin(username, password);
    setTokens(tokenPair.access_token, tokenPair.refresh_token);
    const me = await getMe();
    setUser(me);
  }, []);

  const logout = useCallback(() => {
    clearTokens();
    setUser(null);
    window.location.href = "/login";
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      loading,
      login,
      logout,
      refreshUser,
    }),
    [user, loading, login, logout, refreshUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return value;
}
