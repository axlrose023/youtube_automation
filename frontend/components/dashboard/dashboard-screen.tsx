import { useEffect, useState } from "react";
import { Activity, BarChart3, Eye, Film, Globe, Sparkles } from "lucide-react";

import { BarList } from "@/components/dashboard/bar-list";
import { MetricCard } from "@/components/dashboard/metric-card";
import { RecentSessions } from "@/components/dashboard/recent-sessions";
import { SessionLauncher } from "@/components/dashboard/session-launcher";
import { EmptyState } from "@/components/ui/empty-state";
import { Loader } from "@/components/ui/loader";
import { getDashboardSummary, getEmulationHistory } from "@/lib/api";
import { formatNumber, formatPercent } from "@/lib/format";
import type { EmulationDashboardSummary, EmulationHistoryItem } from "@/types/api";

export function DashboardScreen() {
  const [recentItems, setRecentItems] = useState<EmulationHistoryItem[]>([]);
  const [summary, setSummary] = useState<EmulationDashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      try {
        const [summaryData, recentData] = await Promise.all([
          getDashboardSummary(),
          getEmulationHistory({
            page: 1,
            page_size: 8,
          }),
        ]);

        if (!active) {
          return;
        }
        setSummary(summaryData);
        setRecentItems(recentData.items);
      } catch (err) {
        if (!active) {
          return;
        }
        setError("Не удалось загрузить данные дашборда.");
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void load();
    return () => {
      active = false;
    };
  }, []);

  if (loading) {
    return <Loader label="Загрузка дашборда" />;
  }

  if (error || !summary) {
    return (
      <EmptyState
        title="Дашборд недоступен"
        description={error ?? "Данные дашборда не получены."}
      />
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-[var(--ink)]">Дашборд</h2>
        <p className="mt-1 text-sm text-[var(--muted)]">Обзор активности ваших эмуляций</p>
      </div>

      <div className="metric-grid">
        <MetricCard
          label="Сессии"
          value={formatNumber(summary.total_sessions)}
          note={`${summary.completed} завершено, ${summary.running} активных`}
          icon={Activity}
          color="var(--brand)"
        />
        <MetricCard
          label="Просмотрено реклам"
          value={formatNumber(summary.total_ads_watched)}
          note={`${summary.total_ad_captures} сохранённых артефактов`}
          icon={Eye}
          color="var(--info)"
        />
        <MetricCard
          label="Записано видео"
          value={formatNumber(summary.video_captures)}
          note={`${summary.total_ad_captures > 0 ? formatPercent((summary.video_captures / summary.total_ad_captures) * 100) : "0%"} успешных`}
          icon={Film}
          color="var(--warning)"
        />
        <MetricCard
          label="Сохранения лендингов"
          value={formatNumber(summary.landing_completed)}
          note={`${summary.total_ad_captures > 0 ? formatPercent((summary.landing_completed / summary.total_ad_captures) * 100) : "0%"} успешных`}
          icon={Globe}
          color="var(--danger)"
        />
        <MetricCard
          label="Среднее видео / сессию"
          value={summary.avg_videos_per_session.toFixed(1)}
          note={`${summary.screenshot_fallbacks} переходов на скриншоты`}
          icon={BarChart3}
          color="#a78bfa"
        />
        <MetricCard
          label="Релевантная реклама"
          value={formatNumber(summary.relevant_ads)}
          note={`${summary.analyzed_ads} проанализировано, ${summary.not_relevant_ads} отклонено`}
          icon={Sparkles}
          color="var(--accent)"
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-[1.4fr_0.8fr]">
        <RecentSessions items={recentItems} />
        <SessionLauncher />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <BarList title="Топ доменов рекламодателей" items={summary.top_advertisers} colorClass="bg-sky-500" />
        <BarList title="Топ запрошенных тем" items={summary.top_topics} colorClass="bg-emerald-500" />
      </div>
    </div>
  );
}
