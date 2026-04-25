import { useState } from "react";
import { ExternalLink, Monitor, Save } from "lucide-react";

import { apiClient } from "@/lib/api-client";

type Phase = "idle" | "starting" | "active" | "saving" | "done" | "error";

export function SetupScreen() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [novncUrl, setNovncUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleStart() {
    setPhase("starting");
    setError(null);
    try {
      const { data } = await apiClient.post<{ novnc_url: string; status: string }>(
        "/setup/android-ui/start",
      );
      const url = data.novnc_url;
      setNovncUrl(url);
      setPhase("active");
      window.open(url, "_blank", "noopener,noreferrer");
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
      await apiClient.post("/setup/android-ui/save-and-stop");
      setPhase("done");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Ошибка сохранения";
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
          <span className="text-sm text-[var(--ink-secondary)]">Запускаем эмулятор…</span>
        </div>
      )}

      {(phase === "active" || phase === "saving") && novncUrl && (
        <div className="space-y-4">
          <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-soft)] px-5 py-4">
            <p className="mb-3 text-sm text-[var(--ink-secondary)]">
              Эмулятор запущен. Окно должно было открыться автоматически — если нет, откройте вручную:
            </p>
            <a
              href={novncUrl}
              target="_blank"
              rel="noopener noreferrer"
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
            disabled={phase === "saving"}
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
        </div>
      )}

      {phase === "done" && (
        <div className="rounded-xl border border-green-200 bg-green-50 px-5 py-4 text-sm text-green-800">
          Снэпшот сохранён, эмулятор остановлен. Автоматические сессии возобновлены.
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
