import { useEffect, useMemo, useState } from "react";
import { Activity, BarChart3, Eye, Film, Globe, Sparkles } from "lucide-react";

import { BarList } from "@/components/dashboard/bar-list";
import { MetricCard } from "@/components/dashboard/metric-card";
import { RecentSessions } from "@/components/dashboard/recent-sessions";
import { SessionLauncher } from "@/components/dashboard/session-launcher";
import { EmptyState } from "@/components/ui/empty-state";
import { Loader } from "@/components/ui/loader";
import { getEmulationHistory } from "@/lib/api";
import { formatNumber, formatPercent } from "@/lib/format";
import { aggregateCaptures, topAdvertisers, topTopics } from "@/lib/metrics";
import type { EmulationHistoryItem } from "@/types/api";

export function DashboardScreen() {
  const [items, setItems] = useState<EmulationHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      try {
        const data = await getEmulationHistory({
          page: 1,
          page_size: 100,
          include_captures: true,
        });
        if (!active) {
          return;
        }
        setItems(data.items);
      } catch (err) {
        if (!active) {
          return;
        }
        setError("Failed to load dashboard data.");
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

  const summary = useMemo(() => aggregateCaptures(items), [items]);
  const advertiserItems = useMemo(() => topAdvertisers(summary.captureItems), [summary.captureItems]);
  const topicItems = useMemo(() => topTopics(items), [items]);

  if (loading) {
    return <Loader label="Loading dashboard" />;
  }

  if (error) {
    return (
      <EmptyState
        title="Dashboard unavailable"
        description={error}
      />
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-[var(--ink)]">Dashboard</h2>
        <p className="mt-1 text-sm text-[var(--muted)]">Overview of your emulation activity</p>
      </div>

      <div className="metric-grid">
        <MetricCard
          label="Sessions"
          value={formatNumber(summary.totalSessions)}
          note={`${summary.completed} completed, ${summary.running} running`}
          icon={Activity}
          color="var(--brand)"
        />
        <MetricCard
          label="Ads watched"
          value={formatNumber(summary.totalWatchedAds)}
          note={`${summary.totalAds} captured artifacts`}
          icon={Eye}
          color="var(--info)"
        />
        <MetricCard
          label="Video captures"
          value={formatNumber(summary.videoCaptures)}
          note={`${summary.totalAds > 0 ? formatPercent((summary.videoCaptures / summary.totalAds) * 100) : "0%"} capture success`}
          icon={Film}
          color="var(--warning)"
        />
        <MetricCard
          label="Landing saves"
          value={formatNumber(summary.landingCompleted)}
          note={`${summary.captureItems.length > 0 ? formatPercent((summary.landingCompleted / summary.captureItems.length) * 100) : "0%"} landing success`}
          icon={Globe}
          color="var(--danger)"
        />
        <MetricCard
          label="Avg videos / session"
          value={
            summary.totalSessions > 0
              ? (summary.totalVideos / summary.totalSessions).toFixed(1)
              : "0.0"
          }
          note={`${summary.screenshotFallbacks} screenshot fallbacks`}
          icon={BarChart3}
          color="#a78bfa"
        />
        <MetricCard
          label="Relevant ads"
          value={formatNumber(summary.relevantAds)}
          note={`${summary.analyzedAds} analyzed, ${summary.rejectedAds} rejected`}
          icon={Sparkles}
          color="var(--accent)"
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-[1.4fr_0.8fr]">
        <RecentSessions items={items.slice(0, 8)} />
        <SessionLauncher />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <BarList title="Top advertiser domains" items={advertiserItems} colorClass="bg-sky-500" />
        <BarList title="Top searched topics" items={topicItems} colorClass="bg-emerald-500" />
      </div>
    </div>
  );
}
