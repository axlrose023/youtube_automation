import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Check, ChevronDown, ExternalLink, Globe2, Monitor, Power, Save, UserRound } from "lucide-react";

import { getAndroidAccounts, getProxies } from "@/lib/api";
import { apiClient } from "@/lib/api-client";
import type { AndroidAccountProfile, Proxy } from "@/types/api";

type Phase = "idle" | "starting" | "active" | "saving" | "stopping" | "done" | "error";

type AndroidUiStatus = {
  novnc_url?: string | null;
  status: string;
  message?: string | null;
  serial?: string | null;
  snapshot_name?: string | null;
  snapshot_saved?: boolean | null;
  error?: string | null;
  queue_reason?: string | null;
  android_account_id?: string | null;
  android_google_email?: string | null;
  android_avd_name?: string | null;
  proxy_id?: string | null;
  proxy_label?: string | null;
  proxy_country_code?: string | null;
  proxy_enabled?: boolean;
};

const ACTIVE_STATUSES = new Set(["queued", "starting", "running", "saving", "stopping"]);

function Label({ children }: { children: ReactNode }) {
  return (
    <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-[var(--muted)]">
      {children}
    </div>
  );
}

export function SetupScreen() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [novncUrl, setNovncUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [doneMessage, setDoneMessage] = useState<string>(
    "Эмулятор остановлен. Автоматические сессии возобновлены.",
  );
  const [androidAccounts, setAndroidAccounts] = useState<AndroidAccountProfile[]>([]);
  const [androidAccountId, setAndroidAccountId] = useState("");
  const [accountOpen, setAccountOpen] = useState(false);
  const [proxies, setProxies] = useState<Proxy[]>([]);
  const [proxyId, setProxyId] = useState("");
  const [proxyOpen, setProxyOpen] = useState(false);
  const [activeStatus, setActiveStatus] = useState<AndroidUiStatus | null>(null);
  const accountRef = useRef<HTMLDivElement>(null);
  const proxyRef = useRef<HTMLDivElement>(null);

  const selectedAccount = androidAccounts.find((item) => item.id === androidAccountId) ?? null;
  const selectedProxy = proxies.find((item) => item.id === proxyId) ?? null;
  const busy = ["starting", "active", "saving", "stopping"].includes(phase);

  function complete(status: AndroidUiStatus) {
    setActiveStatus(status);
    setDoneMessage(
      status.snapshot_saved
        ? "Снэпшот сохранён, эмулятор остановлен. Автоматические сессии возобновлены."
        : "Эмулятор остановлен без сохранения. Автоматические сессии возобновлены.",
    );
    setPhase("done");
  }

  function openEmulatorWindow(url: string) {
    window.open(
      url,
      "android-account-setup",
      [
        "popup=yes",
        "width=460",
        "height=920",
        "left=80",
        "top=40",
        "noopener",
        "noreferrer",
      ].join(","),
    );
  }

  function applyStatus(status: AndroidUiStatus) {
    setActiveStatus(status);
    if (status.novnc_url) {
      setNovncUrl(status.novnc_url);
    }
    if (status.android_account_id) {
      setAndroidAccountId(status.android_account_id);
    }
    if (status.proxy_id) {
      setProxyId(status.proxy_id);
    }
    if (status.status === "failed") {
      setError(status.error || "Ошибка настройки Android UI");
      setPhase("error");
      return;
    }
    if (status.status === "saving") {
      setPhase("saving");
      return;
    }
    if (status.status === "stopping") {
      setPhase("stopping");
      return;
    }
    if (ACTIVE_STATUSES.has(status.status)) {
      setPhase(status.status === "queued" || status.status === "starting" ? "starting" : "active");
      return;
    }
    if (status.status === "stopped" && phase !== "idle") {
      complete(status);
    }
  }

  useEffect(() => {
    void getAndroidAccounts(true)
      .then((data) => {
        setAndroidAccounts(data.items);
        const preferred = data.items.find((item) => item.status === "ready") ?? data.items[0];
        if (preferred) setAndroidAccountId(preferred.id);
      })
      .catch(() => undefined);
    void getProxies(true)
      .then((data) => setProxies(data.items))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .get<AndroidUiStatus>("/setup/android-ui/status")
      .then(({ data }) => {
        if (!cancelled && ACTIVE_STATUSES.has(data.status)) {
          applyStatus(data);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!busy) return;
    let cancelled = false;
    const interval = window.setInterval(async () => {
      try {
        const { data } = await apiClient.get<AndroidUiStatus>("/setup/android-ui/status");
        if (!cancelled) {
          applyStatus(data);
        }
      } catch {
        // Explicit action buttons surface request errors; polling stays quiet.
      }
    }, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [busy]);

  useEffect(() => {
    if (!accountOpen) return;
    const onDoc = (event: MouseEvent) => {
      if (accountRef.current && !accountRef.current.contains(event.target as Node)) {
        setAccountOpen(false);
      }
    };
    const onEsc = (event: KeyboardEvent) => {
      if (event.key === "Escape") setAccountOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onEsc);
    };
  }, [accountOpen]);

  useEffect(() => {
    if (!proxyOpen) return;
    const onDoc = (event: MouseEvent) => {
      if (proxyRef.current && !proxyRef.current.contains(event.target as Node)) {
        setProxyOpen(false);
      }
    };
    const onEsc = (event: KeyboardEvent) => {
      if (event.key === "Escape") setProxyOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onEsc);
    };
  }, [proxyOpen]);

  async function handleStart() {
    setPhase("starting");
    setError(null);
    try {
      const { data } = await apiClient.post<AndroidUiStatus>("/setup/android-ui/start", {
        android_account_id: androidAccountId || null,
        proxy_id: proxyId || null,
      });
      const url = data.novnc_url;
      applyStatus(data);
      if (url) {
        setNovncUrl(url);
        openEmulatorWindow(url);
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Ошибка запуска";
      setError(msg);
      setPhase("error");
    }
  }

  async function handleSaveAndStop() {
    setPhase("saving");
    setError(null);
    try {
      const { data } = await apiClient.post<AndroidUiStatus>("/setup/android-ui/save-and-stop");
      complete(data);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Ошибка сохранения";
      setError(msg);
      setPhase("active");
    }
  }

  async function handleStop() {
    setPhase("stopping");
    setError(null);
    try {
      const { data } = await apiClient.post<AndroidUiStatus>("/setup/android-ui/stop");
      complete(data);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Ошибка остановки";
      setError(msg);
      setPhase("active");
    }
  }

  const displayEmail =
    activeStatus?.android_google_email || selectedAccount?.google_email || "Legacy AVD";
  const displayAvd = activeStatus?.android_avd_name || selectedAccount?.avd_name || "default_avd_name";
  const displayProxy = activeStatus?.proxy_enabled
    ? `${activeStatus.proxy_label || selectedProxy?.label || "Proxy"}${activeStatus.proxy_country_code ? ` · ${activeStatus.proxy_country_code}` : ""}`
    : selectedProxy
      ? `${selectedProxy.label}${selectedProxy.country_code ? ` · ${selectedProxy.country_code}` : ""}`
      : "Без прокси";

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-8 md:px-6">
      <div className="space-y-1">
        <h1 className="text-xl font-semibold text-[var(--ink)]">Настройка аккаунта</h1>
        <p className="text-sm text-[var(--muted)]">
          Выберите профиль, откройте его эмулятор и сохраните снэпшот после входа в Google / YouTube.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div ref={accountRef} className="relative">
          <Label>Google аккаунт</Label>
          <button
            type="button"
            disabled={busy}
            onClick={() => setAccountOpen((open) => !open)}
            className="flex h-11 w-full items-center gap-2 rounded-lg px-3 text-[13px] transition disabled:opacity-60"
            style={{
              background: accountOpen ? "var(--panel-soft)" : "var(--panel)",
              boxShadow: "inset 0 0 0 1px var(--line)",
              color: "var(--ink)",
            }}
          >
            <UserRound size={15} className="shrink-0 text-[var(--muted)]" />
            <span className="min-w-0 flex-1 text-left">
              <span className="block truncate font-medium">
                {selectedAccount ? selectedAccount.label : "Legacy AVD"}
              </span>
              <span className="block truncate text-[11px] text-[var(--muted)]">
                {selectedAccount ? selectedAccount.google_email : "default_avd_name"}
              </span>
            </span>
            <ChevronDown
              size={14}
              className="shrink-0 text-[var(--muted)] transition"
              style={{ transform: accountOpen ? "rotate(180deg)" : undefined }}
            />
          </button>
          {accountOpen && androidAccounts.length > 0 && (
            <div
              className="absolute left-0 right-0 z-30 max-h-64 overflow-y-auto rounded-xl"
              style={{
                top: "calc(100% + 6px)",
                background: "var(--panel)",
                boxShadow: "0 8px 24px rgba(0,0,0,0.10), inset 0 0 0 1px var(--line)",
              }}
            >
              {androidAccounts.map((account) => {
                const selected = account.id === androidAccountId;
                return (
                  <button
                    key={account.id}
                    type="button"
                    onClick={() => {
                      setAndroidAccountId(account.id);
                      setAccountOpen(false);
                    }}
                    className="flex h-12 w-full items-center gap-2.5 px-3 text-left text-[13px]"
                    style={{ background: selected ? "var(--panel-soft)" : undefined }}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[var(--ink)]">{account.label}</span>
                      <span className="block truncate text-[11px] text-[var(--muted)]">
                        {account.google_email}
                      </span>
                    </span>
                    <span className="font-mono text-[10.5px] text-[var(--muted)]">
                      {account.emulator_port ?? "auto"}
                    </span>
                    {selected && <Check size={14} className="text-[var(--ink-secondary)]" />}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div ref={proxyRef} className="relative">
          <Label>Прокси</Label>
          <button
            type="button"
            disabled={busy}
            onClick={() => setProxyOpen((open) => !open)}
            className="flex h-11 w-full items-center gap-2 rounded-lg px-3 text-[13px] transition disabled:opacity-60"
            style={{
              background: proxyOpen ? "var(--panel-soft)" : "var(--panel)",
              boxShadow: "inset 0 0 0 1px var(--line)",
              color: "var(--ink)",
            }}
          >
            <Globe2 size={15} className="shrink-0 text-[var(--muted)]" />
            <span className="min-w-0 flex-1 text-left">
              <span className="block truncate font-medium">
                {selectedProxy ? selectedProxy.label : "Без прокси"}
              </span>
              <span className="block truncate text-[11px] text-[var(--muted)]">
                {selectedProxy?.country_code || "direct"}
              </span>
            </span>
            <ChevronDown
              size={14}
              className="shrink-0 text-[var(--muted)] transition"
              style={{ transform: proxyOpen ? "rotate(180deg)" : undefined }}
            />
          </button>
          {proxyOpen && (
            <div
              className="absolute left-0 right-0 z-30 max-h-64 overflow-y-auto rounded-xl"
              style={{
                top: "calc(100% + 6px)",
                background: "var(--panel)",
                boxShadow: "0 8px 24px rgba(0,0,0,0.10), inset 0 0 0 1px var(--line)",
              }}
            >
              <button
                type="button"
                onClick={() => {
                  setProxyId("");
                  setProxyOpen(false);
                }}
                className="flex h-10 w-full items-center gap-2.5 px-3 text-left text-[13px]"
                style={{ background: proxyId ? undefined : "var(--panel-soft)" }}
              >
                <span className="min-w-0 flex-1 truncate text-[var(--ink)]">Без прокси</span>
                {!proxyId && <Check size={14} className="text-[var(--ink-secondary)]" />}
              </button>
              {proxies.map((proxy) => {
                const selected = proxy.id === proxyId;
                return (
                  <button
                    key={proxy.id}
                    type="button"
                    onClick={() => {
                      setProxyId(proxy.id);
                      setProxyOpen(false);
                    }}
                    className="flex h-10 w-full items-center gap-2.5 px-3 text-left text-[13px]"
                    style={{ background: selected ? "var(--panel-soft)" : undefined }}
                  >
                    <span className="min-w-0 flex-1 truncate text-[var(--ink)]">{proxy.label}</span>
                    <span className="font-mono text-[11px] text-[var(--muted)]">
                      {proxy.country_code ?? ""}
                    </span>
                    {selected && <Check size={14} className="text-[var(--ink-secondary)]" />}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>

      <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-soft)] px-5 py-4">
        <div className="grid grid-cols-1 gap-3 text-sm md:grid-cols-3">
          <div>
            <div className="text-[11px] uppercase tracking-wider text-[var(--muted)]">Аккаунт</div>
            <div className="mt-1 truncate font-medium text-[var(--ink)]">{displayEmail}</div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wider text-[var(--muted)]">AVD</div>
            <div className="mt-1 truncate font-mono text-[12px] text-[var(--ink)]">{displayAvd}</div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wider text-[var(--muted)]">Сеть</div>
            <div className="mt-1 truncate font-medium text-[var(--ink)]">{displayProxy}</div>
          </div>
        </div>
      </div>

      {phase === "idle" && (
        <button
          onClick={handleStart}
          className="inline-flex items-center gap-2 rounded-lg bg-[var(--brand)] px-5 py-2.5 text-sm font-medium text-white transition hover:opacity-90"
        >
          <Monitor size={16} />
          Открыть эмулятор
        </button>
      )}

      {phase === "starting" && (
        <div className="flex items-center gap-3 rounded-xl border border-[var(--line)] bg-[var(--panel-soft)] px-5 py-4">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-[var(--brand)] border-t-transparent" />
          <span className="text-sm text-[var(--ink-secondary)]">
            {activeStatus?.queue_reason || "Ждём Android worker и запускаем эмулятор..."}
          </span>
        </div>
      )}

      {(["starting", "active", "saving", "stopping"].includes(phase)) && novncUrl && (
        <div className="space-y-4">
          <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-soft)] px-5 py-4">
            <a
              href={novncUrl}
              target="android-account-setup"
              rel="noopener noreferrer"
              onClick={(event) => {
                event.preventDefault();
                openEmulatorWindow(novncUrl);
              }}
              className="inline-flex items-center gap-2 rounded-lg border border-[var(--brand)] px-4 py-2 text-sm font-medium text-[var(--brand)] transition hover:bg-[var(--brand-soft)]"
            >
              <ExternalLink size={14} />
              Открыть окно эмулятора
            </a>
            {activeStatus?.serial && (
              <span className="ml-3 font-mono text-[12px] text-[var(--muted)]">
                {activeStatus.serial}
              </span>
            )}
          </div>

          <button
            onClick={handleSaveAndStop}
            disabled={phase === "saving" || phase === "stopping"}
            className="inline-flex items-center gap-2 rounded-lg bg-[#16a34a] px-5 py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-60"
          >
            {phase === "saving" ? (
              <>
                <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent" />
                Сохраняем...
              </>
            ) : (
              <>
                <Save size={15} />
                Сохранить и завершить
              </>
            )}
          </button>

          <button
            onClick={handleStop}
            disabled={phase === "saving" || phase === "stopping"}
            className="ml-3 inline-flex items-center gap-2 rounded-lg border border-[var(--line)] px-5 py-2.5 text-sm font-medium text-[var(--ink-secondary)] transition hover:bg-[var(--panel-soft)] disabled:opacity-60"
          >
            {phase === "stopping" ? (
              <>
                <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-[var(--muted)] border-t-transparent" />
                Останавливаем...
              </>
            ) : (
              <>
                <Power size={15} />
                Остановить без сохранения
              </>
            )}
          </button>
        </div>
      )}

      {phase === "done" && (
        <div className="rounded-xl border border-green-200 bg-green-50 px-5 py-4 text-sm text-green-800">
          {doneMessage}
        </div>
      )}

      {phase === "error" && error && (
        <div className="space-y-3">
          <div className="rounded-xl border border-red-200 bg-red-50 px-5 py-4 text-sm text-red-800">
            {error}
          </div>
          <button
            onClick={handleStart}
            className="inline-flex items-center gap-2 rounded-lg bg-[var(--brand)] px-5 py-2.5 text-sm font-medium text-white transition hover:opacity-90"
          >
            <Monitor size={16} />
            Попробовать снова
          </button>
        </div>
      )}
    </div>
  );
}
