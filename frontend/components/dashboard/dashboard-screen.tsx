import { useEffect, useMemo, useState } from "react";
import {
  Clapperboard,
  Globe2,
  Megaphone,
  Radar,
  SearchCheck,
  Tv,
} from "lucide-react";

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
    <div className="space-y-8">
      <div className="grid gap-8 xl:grid-cols-[1.15fr_0.85fr]">
        <section className="hero-panel p-8 text-white">
          <div className="relative z-10">
            <div className="section-eyebrow text-white/60">Mission control</div>
            <h2 className="mt-3 max-w-3xl text-4xl font-semibold leading-tight">
              Observe session quality, ad capture, and relevance from one control room.
            </h2>
            <p className="mt-4 max-w-2xl text-sm leading-7 text-white/72">
              This workspace is tuned for operational review: live runs, post-session audits,
              saved media, and quick launch stay within the same panel.
            </p>
            <div className="mt-6 flex flex-wrap gap-3">
              <div className="info-chip">
                <span className="inline-flex h-2.5 w-2.5 rounded-full bg-emerald-400" />
                {summary.running} active now
              </div>
              <div className="info-chip">
                <span className="inline-flex h-2.5 w-2.5 rounded-full bg-sky-400" />
                {summary.totalAds > 0
                  ? formatPercent((summary.videoCaptures / summary.totalAds) * 100)
                  : "0%"} capture success
              </div>
              <div className="info-chip">
                <span className="inline-flex h-2.5 w-2.5 rounded-full bg-amber-300" />
                {summary.relevantAds} relevant ads
              </div>
            </div>
            <div className="mt-8 grid gap-4 md:grid-cols-3">
              <div className="rounded-3xl border border-white/10 bg-white/6 px-4 py-4 backdrop-blur-sm">
                <div className="text-xs uppercase tracking-[0.18em] text-white/55">Sessions</div>
                <div className="mt-2 text-3xl font-semibold">{formatNumber(summary.totalSessions)}</div>
                <div className="mt-2 text-sm text-white/65">{summary.completed} completed runs</div>
              </div>
              <div className="rounded-3xl border border-white/10 bg-white/6 px-4 py-4 backdrop-blur-sm">
                <div className="text-xs uppercase tracking-[0.18em] text-white/55">Ads watched</div>
                <div className="mt-2 text-3xl font-semibold">{formatNumber(summary.totalWatchedAds)}</div>
                <div className="mt-2 text-sm text-white/65">{summary.totalAds} saved capture artifacts</div>
              </div>
              <div className="rounded-3xl border border-white/10 bg-white/6 px-4 py-4 backdrop-blur-sm">
                <div className="text-xs uppercase tracking-[0.18em] text-white/55">Landing saves</div>
                <div className="mt-2 text-3xl font-semibold">{formatNumber(summary.landingCompleted)}</div>
                <div className="mt-2 text-sm text-white/65">
                  {summary.captureItems.length > 0
                    ? formatPercent((summary.landingCompleted / summary.captureItems.length) * 100)
                    : "0%"} success rate
                </div>
              </div>
            </div>
          </div>
        </section>

        <SessionLauncher />
      </div>

      <div className="metric-grid">
        <MetricCard
          label="Sessions"
          value={formatNumber(summary.totalSessions)}
          note={`${summary.completed} completed, ${summary.running} running`}
          icon={Tv}
        />
        <MetricCard
          label="Ads watched"
          value={formatNumber(summary.totalWatchedAds)}
          note={`${summary.totalAds} captured artifacts`}
          accent="bg-sky-50 text-sky-700"
          icon={Megaphone}
        />
        <MetricCard
          label="Video captures"
          value={formatNumber(summary.videoCaptures)}
          note={`${summary.totalAds > 0 ? formatPercent((summary.videoCaptures / summary.totalAds) * 100) : "0%"} capture success`}
          accent="bg-amber-50 text-amber-700"
          icon={Clapperboard}
        />
        <MetricCard
          label="Landing saves"
          value={formatNumber(summary.landingCompleted)}
          note={`${summary.captureItems.length > 0 ? formatPercent((summary.landingCompleted / summary.captureItems.length) * 100) : "0%"} landing success`}
          accent="bg-rose-50 text-rose-700"
          icon={Globe2}
        />
        <MetricCard
          label="Avg videos / session"
          value={
            summary.totalSessions > 0
              ? (summary.totalVideos / summary.totalSessions).toFixed(1)
              : "0.0"
          }
          note={`${summary.screenshotFallbacks} screenshot fallbacks`}
          accent="bg-violet-50 text-violet-700"
          icon={Radar}
        />
        <MetricCard
          label="Relevant ads"
          value={formatNumber(summary.relevantAds)}
          note={`${summary.analyzedAds} analyzed, ${summary.rejectedAds} rejected`}
          accent="bg-emerald-50 text-emerald-700"
          icon={SearchCheck}
        />
      </div>

      <div className="grid gap-8 xl:grid-cols-[1.4fr_0.8fr]">
        <RecentSessions items={items.slice(0, 8)} />
        <div className="space-y-8">
          <BarList title="Top advertiser domains" items={advertiserItems} colorClass="bg-sky-500" />
          <BarList title="Top searched topics" items={topicItems} colorClass="bg-emerald-500" />
        </div>
      </div>
    </div>
  );
}
