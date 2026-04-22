"use client";

import { apiClient } from "@/lib/api-client";
import { clearTokens, getAccessToken, getRefreshToken, setTokens } from "@/lib/tokens";
import type {
  EmulationDashboardSummary,
  EmulationSessionActionRequest,
  EmulationHistoryDetail,
  EmulationHistoryItem,
  EmulationSessionStatus,
  EmulationStatusBatchResponse,
  Pagination,
  Proxy,
  ProxyCreate,
  ProxyListResponse,
  StartEmulationRequest,
  StartEmulationResponse,
  TokenPair,
  User,
} from "@/types/api";

const apiBaseUrl = import.meta.env.VITE_API_URL ?? "/api";

function shouldKeepStatusStreamOpen(status: EmulationSessionStatus | null) {
  if (!status) {
    return true;
  }

  const terminal = status.status === "completed" || status.status === "failed" || status.status === "stopped";
  const postProcessingActive =
    status.post_processing_status === "queued" || status.post_processing_status === "running";
  return !terminal || postProcessingActive;
}

async function refreshStreamAccessToken() {
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    clearTokens();
    throw new Error("Missing refresh token for status stream");
  }

  const { data } = await apiClient.post<TokenPair>("/auth/refresh", {
    refresh_token: refreshToken,
  });
  setTokens(data.access_token, data.refresh_token);
  return data.access_token;
}

export async function login(username: string, password: string) {
  const { data } = await apiClient.post<TokenPair>("/auth/login", { username, password });
  return data;
}

export async function getMe() {
  const { data } = await apiClient.get<User>("/users/me");
  return data;
}

export async function getUsers(page = 1, pageSize = 20) {
  const { data } = await apiClient.get<Pagination<User>>("/users", {
    params: { page, page_size: pageSize },
  });
  return data;
}

export async function createUser(payload: {
  username: string;
  password: string;
  is_admin?: boolean;
}) {
  const { data } = await apiClient.post<User>("/users", payload);
  return data;
}

export async function updateUser(
  userId: string,
  payload: Partial<{
    username: string;
    password: string;
    is_admin: boolean;
    is_active: boolean;
  }>,
) {
  const { data } = await apiClient.patch<User>(`/users/${userId}`, payload);
  return data;
}

export async function deleteUser(userId: string) {
  await apiClient.delete(`/users/${userId}`);
}

export async function getEmulationHistory(params: Record<string, string | number | boolean | undefined>) {
  const { data } = await apiClient.get<Pagination<EmulationHistoryItem>>("/emulation/history", {
    params,
  });
  return data;
}

export async function getEmulationDetail(sessionId: string) {
  const { data } = await apiClient.get<EmulationHistoryDetail>(`/emulation/history/${sessionId}`, {
    params: {
      include_captures: true,
      include_raw_ads: true,
    },
  });
  return data;
}

export async function getEmulationStatus(sessionId: string) {
  const { data } = await apiClient.get<EmulationSessionStatus>(`/emulation/${sessionId}/status`);
  return data;
}

export async function getEmulationStatusBatch(sessionIds: string[]) {
  const { data } = await apiClient.post<EmulationStatusBatchResponse>("/emulation/status/batch", {
    session_ids: sessionIds,
  });
  return data;
}

export async function getDashboardSummary() {
  const { data } = await apiClient.get<EmulationDashboardSummary>("/emulation/dashboard/summary");
  return data;
}

export function streamEmulationStatus(
  sessionId: string,
  handlers: {
    onStatus: (status: EmulationSessionStatus) => void;
    onClose?: () => void;
    onError?: (error: unknown) => void;
  },
) {
  const controller = new AbortController();
  let lastStatus: EmulationSessionStatus | null = null;

  const parseChunk = (chunk: string) => {
    const dataLines = chunk
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart());

    if (dataLines.length === 0) {
      return;
    }

    const payload = JSON.parse(dataLines.join("\n")) as EmulationSessionStatus;
    lastStatus = payload;
    handlers.onStatus(payload);
  };

  const openStream = async (allowRefresh = true): Promise<Response> => {
    const token = getAccessToken();
    const response = await fetch(`${apiBaseUrl}/emulation/${sessionId}/status/stream`, {
      method: "GET",
      headers: {
        Accept: "text/event-stream",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      signal: controller.signal,
    });

    if (response.status === 401 && allowRefresh) {
      await refreshStreamAccessToken();
      return openStream(false);
    }

    if (!response.ok || !response.body) {
      throw new Error(`Failed to open status stream (${response.status})`);
    }

    return response;
  };

  const run = async () => {
    const response = await openStream();
    const body = response.body;
    if (!body) {
      throw new Error("Status stream has no body");
    }

    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() ?? "";

      for (const chunk of chunks) {
        parseChunk(chunk);
      }
    }

    const tail = decoder.decode();
    if (tail) {
      buffer += tail;
    }
    if (buffer.trim()) {
      parseChunk(buffer);
    }

    if (shouldKeepStatusStreamOpen(lastStatus)) {
      throw new Error("Status stream closed before session finished");
    }

    handlers.onClose?.();
  };

  void run().catch((error) => {
    if (!controller.signal.aborted) {
      handlers.onError?.(error);
    }
  });

  return () => controller.abort();
}

export async function startEmulation(payload: StartEmulationRequest) {
  const { data } = await apiClient.post<StartEmulationResponse>("/emulation/start", payload);
  return data;
}

export async function stopEmulation(sessionId: string) {
  const payload: EmulationSessionActionRequest = { session_id: sessionId };
  const { data } = await apiClient.post<{ session_id: string; status: string }>(
    "/emulation/stop",
    payload,
  );
  return data;
}

export async function retryEmulation(sessionId: string) {
  const payload: EmulationSessionActionRequest = { session_id: sessionId };
  const { data } = await apiClient.post<StartEmulationResponse>("/emulation/retry", payload);
  return data;
}

export async function resumeEmulation(sessionId: string) {
  const payload: EmulationSessionActionRequest = { session_id: sessionId };
  const { data } = await apiClient.post<StartEmulationResponse>("/emulation/resume", payload);
  return data;
}

export async function getProxies(activeOnly = false) {
  const { data } = await apiClient.get<ProxyListResponse>("/proxies", {
    params: { active_only: activeOnly },
  });
  return data;
}

export async function createProxy(payload: ProxyCreate) {
  const { data } = await apiClient.post<Proxy>("/proxies", payload);
  return data;
}

export async function deleteProxy(proxyId: string) {
  await apiClient.delete(`/proxies/${proxyId}`);
}
