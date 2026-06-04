import axios from "axios";

import { clearTokens, getAccessToken, getRefreshToken, setTokens } from "@/lib/tokens";

const apiBaseUrl = import.meta.env.VITE_API_URL ?? "/api";

export const apiClient = axios.create({
  baseURL: apiBaseUrl,
  headers: {
    "Content-Type": "application/json",
  },
});

apiClient.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

let isRefreshing = false;
let queue: Array<{
  resolve: (value: string) => void;
  reject: (reason?: unknown) => void;
}> = [];

function flushQueue(error?: unknown, token?: string) {
  for (const entry of queue) {
    if (error) {
      entry.reject(error);
    } else if (token) {
      entry.resolve(token);
    }
  }
  queue = [];
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    const status = error.response?.status;
    const requestUrl = String(originalRequest?.url ?? "");
    const isAuthRoute =
      requestUrl.includes("/auth/login") || requestUrl.includes("/auth/refresh");

    if (status !== 401 || !originalRequest || originalRequest._retry || isAuthRoute) {
      return Promise.reject(error);
    }

    const refreshToken = getRefreshToken();
    if (!refreshToken) {
      clearTokens();
      if (typeof window !== "undefined") {
        window.location.href = "/login";
      }
      return Promise.reject(error);
    }

    if (isRefreshing) {
      return new Promise((resolve, reject) => {
        queue.push({ resolve, reject });
      }).then((token) => {
        originalRequest.headers.Authorization = `Bearer ${token}`;
        return apiClient(originalRequest);
      });
    }

    originalRequest._retry = true;
    isRefreshing = true;

    try {
      const response = await axios.post(`${apiBaseUrl}/auth/refresh`, {
        refresh_token: refreshToken,
      });
      const nextAccessToken = response.data.access_token as string;
      const nextRefreshToken = response.data.refresh_token as string;
      setTokens(nextAccessToken, nextRefreshToken);
      flushQueue(undefined, nextAccessToken);
      originalRequest.headers.Authorization = `Bearer ${nextAccessToken}`;
      return apiClient(originalRequest);
    } catch (refreshError) {
      flushQueue(refreshError);
      clearTokens();
      if (typeof window !== "undefined") {
        window.location.href = "/login";
      }
      return Promise.reject(refreshError);
    } finally {
      isRefreshing = false;
    }
  },
);
