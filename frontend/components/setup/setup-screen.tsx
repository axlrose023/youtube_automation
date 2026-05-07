import { useEffect, useState } from "react";
import { ExternalLink, Monitor, Power, Save } from "lucide-react";

import { apiClient } from "@/lib/api-client";

type Phase = "idle" | "starting" | "active" | "saving" | "stopping" | "done" | "error";

type AndroidUiStatus = {
  novnc_url?: string | null;
  status: string;
  message?: string | null;
  snapshot_name?: string | null;
  snapshot_saved?: boolean | null;
  error?: string | null;
};

const ACTIVE_STATUSES = new Set(["queued", "starting", "running", "saving", "stopping"]);

export function SetupScreen() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [novncUrl, setNovncUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [doneMessage, setDoneMessage] = useState<string>(
    "Эмулятор остановлен. Автоматические сессии возобновлены.",
  );

  function complete(status: AndroidUiStatus) {
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
    if (status.novnc_url) {
      setNovncUrl(status.novnc_url);
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
    if (!["starting", "active", "saving", "stopping"].includes(phase)) {
      return;
    }
    let cancelled = false;
    const interval = window.setInterval(async () => {
      try {
        const { data } = await apiClient.get<AndroidUiStatus>("/setup/android-ui/status");
        if (!cancelled) {
          applyStatus(data);
        }
      } catch {
        // The explicit action buttons surface request errors; polling stays quiet.
      }
    }, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [phase]);

  async function handleStart() {
    setPhase("starting");
    setError(null);
    try {
      const { data } = await apiClient.post<{ novnc_url: string; status: string }>(
        "/setup/android-ui/start",
      );
      const url = data.novnc_url;
      setNovncUrl(url);
      setPhase(data.status === "queued" || data.status === "starting" ? "starting" : "active");
      openEmulatorWindow(url);
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

  return (
    <div className="mx-auto max-w-2xl space-y-6 px-4 py-8 md:px-6">
      <div className="space-y-1">
        <h1 className="text-xl font-semibold text-[var(--ink)]">Настройка аккаунта</h1>
        <p className="text-sm text-[var(--muted)]">
          Запустите Android-эмулятор, настройте аккаунт Google / YouTube, затем сохраните снэпшот.
        </p>
      </div>

      {phase === "idle" && (
        <button
          onClick={handleStart}
          className="inline-flex items-center gap-2 rounded-lg bg-[var(--brand)] px-5 py-2.5 text-sm font-medium text-white transition hover:opacity-90"
        >
          <Monitor size={16} />
          Настроить аккаунт
        </button>
      )}

      {phase === "starting" && (
        <div className="flex items-center gap-3 rounded-xl border border-[var(--line)] bg-[var(--panel-soft)] px-5 py-4">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-[var(--brand)] border-t-transparent" />
          <span className="text-sm text-[var(--ink-secondary)]">
            Ждём Android worker и запускаем эмулятор…
          </span>
        </div>
      )}

      {(["starting", "active", "saving", "stopping"].includes(phase)) && novncUrl && (
        <div className="space-y-4">
          <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-soft)] px-5 py-4">
            <p className="mb-3 text-sm text-[var(--ink-secondary)]">
              Эмулятор запущен. Окно должно было открыться автоматически — если нет, откройте вручную:
            </p>
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
              Открыть эмулятор
            </a>
          </div>

          <p className="text-sm text-[var(--muted)]">
            Когда закончите настройку — нажмите кнопку ниже чтобы сохранить снэпшот и остановить эмулятор.
          </p>

          <button
            onClick={handleSaveAndStop}
            disabled={phase === "saving" || phase === "stopping"}
            className="inline-flex items-center gap-2 rounded-lg bg-[#16a34a] px-5 py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-60"
          >
            {phase === "saving" ? (
              <>
                <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent" />
                Сохраняем…
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
                Останавливаем…
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
