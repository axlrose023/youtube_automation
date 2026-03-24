import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ChevronDown,
  ChevronUp,
  FileImage,
  Film,
  FolderOpen,
  Globe,
  PlayCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Loader } from "@/components/ui/loader";
import {
  getEmulationDetail,
  getEmulationStatus,
  resumeEmulation,
  retryEmulation,
  stopEmulation,
} from "@/lib/api";
import { formatBytes, formatDate, formatMinutes, formatNumber } from "@/lib/format";
import { getStatusTone } from "@/lib/metrics";
import { getAccessToken } from "@/lib/tokens";
import type {
  EmulationAdCapture,
  EmulationHistoryDetail,
  EmulationSessionStatus,
  EmulationWatchedAd,
} from "@/types/api";

type AdCard = {
  position: number;
  ad: EmulationWatchedAd | null;
  captures: EmulationAdCapture[];
  primaryCapture: EmulationAdCapture | null;
};

function readAnalysisField(
  capture: EmulationAdCapture | null,
  key: string,
): string | null {
  const value = capture?.analysis_summary?.[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function getAnalysisTone(result: string | null) {
  if (result === "relevant") {
    return "success";
  }
  if (result === "not_relevant") {
    return "warning";
  }
  return "neutral";
}

function getAnalysisLabel(result: string | null, status?: string | null) {
  if (result === "relevant") {
    return "Relevant";
  }
  if (result === "not_relevant") {
    return "Not relevant";
  }
  if (status === "failed") {
    return "Analysis failed";
  }
  if (status === "pending") {
    return "Analysis pending";
  }
  return "No analysis";
}

function getPreviewText(ad: EmulationWatchedAd | null) {
  const text = ad?.full_text?.replace(/\s+/g, " ").trim();
  if (!text) {
    return null;
  }
  if (text.length <= 140) {
    return text;
  }
  return `${text.slice(0, 140).trim()}...`;
}

function getBaseName(value: string | null | undefined) {
  if (!value) {
    return null;
  }
  const normalized = value.replace(/\\/g, "/");
  const parts = normalized.split("/").filter(Boolean);
  return parts.at(-1) ?? value;
}

function getLandingHost(value: string | null | undefined) {
  if (!value) {
    return null;
  }

  try {
    return new URL(value).hostname;
  } catch {
    return value;
  }
}

function buildMediaUrl(value: string | null | undefined) {
  if (!value) {
    return null;
  }
  const accessToken = getAccessToken();
  if (!accessToken) {
    return null;
  }
  const encoded = value
    .split("/")
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  return `/api/emulation/media/${encoded}?access_token=${encodeURIComponent(accessToken)}`;
}

function buildLiveCapture(ad: EmulationWatchedAd | null): EmulationAdCapture | null {
  const capture = ad?.capture;
  if (!ad || !capture) {
    return null;
  }

  return {
    ad_position: ad.position,
    advertiser_domain: ad.advertiser_domain ?? null,
    cta_href: null,
    display_url: ad.display_url ?? null,
    headline_text: ad.headline_text ?? null,
    ad_duration_seconds: ad.ad_duration_seconds ?? null,
    landing_url: capture.landing_url ?? null,
    landing_dir: capture.landing_dir ?? null,
    landing_status: capture.landing_status ?? "pending",
    video_src_url: capture.video_src_url ?? null,
    video_file: capture.video_file ?? null,
    video_status: capture.video_status ?? "pending",
    analysis_status: null,
    analysis_summary: null,
    screenshot_paths: capture.screenshot_paths ?? [],
  };
}

export function SessionDetailScreen({ sessionId }: { sessionId: string }) {
  const navigate = useNavigate();
  const [detail, setDetail] = useState<EmulationHistoryDetail | null>(null);
  const [liveStatus, setLiveStatus] = useState<EmulationSessionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"overview" | "ads">("overview");
  const [expandedAd, setExpandedAd] = useState<number | null>(null);
  const [showAllAds, setShowAllAds] = useState(false);

  useEffect(() => {
    let active = true;
    let timer: number | undefined;

    async function pull() {
      try {
        const data = await getEmulationDetail(sessionId);
        if (!active) {
          return;
        }
        setDetail(data);
        if (data.status === "running" || data.status === "queued") {
          const runtime = await getEmulationStatus(sessionId);
          if (!active) {
            return;
          }
          setLiveStatus(runtime);
          timer = window.setTimeout(() => {
            void pull();
          }, 2500);
        } else {
          setLiveStatus(null);
        }
      } catch (err) {
        if (active) {
          setError("Failed to load session detail.");
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void pull();
    return () => {
      active = false;
      if (timer) {
        window.clearTimeout(timer);
      }
    };
  }, [sessionId]);

  const session = useMemo<EmulationHistoryDetail>(() => {
    if (!detail) {
      return null as never;
    }
    if (!liveStatus) {
      return detail;
    }

    const liveVideoCaptures = liveStatus.watched_ads.filter(
      (ad) => ad.capture?.video_status === "completed",
    ).length;
    const liveScreenshotFallbacks = liveStatus.watched_ads.filter(
      (ad) =>
        (ad.capture?.screenshot_paths?.length ?? 0) > 0 &&
        ad.capture?.video_status !== "completed",
    ).length;

    return {
      ...detail,
      status: liveStatus.status,
      elapsed_minutes: liveStatus.elapsed_minutes ?? detail.elapsed_minutes,
      bytes_downloaded: liveStatus.bytes_downloaded,
      topics_searched: liveStatus.topics_searched,
      videos_watched: liveStatus.videos_watched,
      watched_videos_count: liveStatus.watched_videos_count,
      watched_videos: liveStatus.watched_videos,
      watched_ads_count: liveStatus.watched_ads_count,
      watched_ads: liveStatus.watched_ads,
      watched_ads_analytics: liveStatus.watched_ads_analytics,
      mode: liveStatus.mode ?? detail.mode,
      fatigue: liveStatus.fatigue ?? detail.fatigue,
      captures: {
        ads_total: liveStatus.watched_ads_count,
        video_captures: liveVideoCaptures,
        screenshot_fallbacks: liveScreenshotFallbacks,
      },
    };
  }, [detail, liveStatus]);

  const adCards = useMemo<AdCard[]>(() => {
    const ads = session?.watched_ads ?? [];
    const captures = session?.ad_captures ?? [];
    const positions = new Set<number>();

    for (const ad of ads) {
      positions.add(ad.position);
    }
    for (const capture of captures) {
      positions.add(capture.ad_position);
    }

    return [...positions]
      .sort((left, right) => left - right)
      .map((position) => {
        const ad = ads.find((item) => item.position === position) ?? null;
        const persistedCaptures = captures.filter((item) => item.ad_position === position);
        const liveCapture = buildLiveCapture(ad);
        const adCaptures = persistedCaptures.length > 0
          ? persistedCaptures
          : liveCapture
            ? [liveCapture]
            : [];

        return {
          position,
          ad,
          captures: adCaptures,
          primaryCapture: adCaptures[0] ?? null,
        };
      });
  }, [session]);

  const hasAds = adCards.length > 0;
  const visibleAdCards = showAllAds ? adCards : adCards.slice(0, 4);

  async function handleAction(action: "stop" | "retry" | "resume") {
    if (!session) {
      return;
    }

    try {
      if (action === "stop") {
        await stopEmulation(session.session_id);
      } else if (action === "retry") {
        const response = await retryEmulation(session.session_id);
        navigate(`/sessions/${response.session_id}`);
        return;
      } else {
        const response = await resumeEmulation(session.session_id);
        navigate(`/sessions/${response.session_id}`);
        return;
      }
      const nextDetail = await getEmulationDetail(sessionId);
      setDetail(nextDetail);
    } catch (err) {
      setError(`Failed to ${action} session.`);
    }
  }

  if (loading) {
    return <Loader label="Loading session detail" />;
  }

  if (error || !session) {
    return (
      <EmptyState
        title="Session unavailable"
        description={error ?? "No detail returned."}
      />
    );
  }

  return (
    <div className="space-y-6">
      <section className="hero-panel p-8 text-white">
        <div className="relative z-10 grid gap-8 xl:grid-cols-[1.2fr_0.8fr]">
          <div>
            <Link to="/sessions" className="text-sm font-semibold text-white/72 transition hover:text-white">
              Back to sessions
            </Link>
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <Badge tone={getStatusTone(session.status) as never}>{session.status}</Badge>
              <span className="text-sm text-white/65">{formatDate(session.queued_at)}</span>
              {liveStatus?.profile_id ? (
                <span className="info-chip px-3 py-2 text-[0.72rem]">
                  profile {liveStatus.profile_id}
                </span>
              ) : null}
            </div>
            <h2 className="mt-4 text-3xl font-semibold tracking-tight">
              Session {session.session_id.slice(0, 12)}
            </h2>
            <p className="mt-3 max-w-2xl text-sm leading-7 text-white/72">
              Review runtime behavior, watched videos, captured ads, and post-session analysis
              from a single inspection surface.
            </p>
            <div className="mt-6 flex flex-wrap gap-3">
              {(session.topics_searched.length > 0 ? session.topics_searched : session.requested_topics).map((topic) => (
                <span key={topic} className="info-chip">
                  {topic}
                </span>
              ))}
            </div>
          </div>
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="rounded-3xl border border-white/10 bg-white/7 p-5 backdrop-blur-sm">
                <div className="text-xs uppercase tracking-[0.18em] text-white/55">Requested</div>
                <div className="mt-3 text-4xl font-semibold">{session.requested_duration_minutes}m</div>
                <div className="mt-2 text-sm text-white/68">Actual {formatMinutes(session.elapsed_minutes)}</div>
              </div>
              <div className="rounded-3xl border border-white/10 bg-white/7 p-5 backdrop-blur-sm">
                <div className="text-xs uppercase tracking-[0.18em] text-white/55">Videos</div>
                <div className="mt-3 text-4xl font-semibold">{formatNumber(session.watched_videos_count)}</div>
                <div className="mt-2 text-sm text-white/68">
                  {formatNumber(session.videos_watched)} completed watch actions
                </div>
              </div>
              <div className="rounded-3xl border border-white/10 bg-white/7 p-5 backdrop-blur-sm">
                <div className="text-xs uppercase tracking-[0.18em] text-white/55">Ads</div>
                <div className="mt-3 text-4xl font-semibold">{formatNumber(session.watched_ads_count)}</div>
                <div className="mt-2 text-sm text-white/68">{session.captures.video_captures} video captures</div>
              </div>
              <div className="rounded-3xl border border-white/10 bg-white/7 p-5 backdrop-blur-sm">
                <div className="text-xs uppercase tracking-[0.18em] text-white/55">Traffic</div>
                <div className="mt-3 text-4xl font-semibold">{formatBytes(session.bytes_downloaded)}</div>
                <div className="mt-2 text-sm text-white/68">downloaded traffic</div>
              </div>
            </div>
            <div className="flex flex-wrap gap-3">
              {session.status === "running" ? (
                <Button variant="danger" onClick={() => void handleAction("stop")}>
                  Stop
                </Button>
              ) : null}
              {session.status === "failed" ? (
                <Button variant="secondary" onClick={() => void handleAction("retry")}>
                  Retry
                </Button>
              ) : null}
              {session.status === "stopped" ? (
                <Button onClick={() => void handleAction("resume")}>Resume remaining</Button>
              ) : null}
            </div>
          </div>
        </div>
      </section>

      {liveStatus ? (
        <div className="grid gap-6 xl:grid-cols-[1fr_1fr]">
          <Card className="p-6">
            <div className="section-eyebrow">Live runtime</div>
            <div className="mt-2 text-lg font-semibold text-[var(--ink)]">Session heartbeat</div>
            <div className="mt-5 space-y-4 text-sm">
              <div className="flex justify-between gap-4">
                <span className="text-[var(--muted)]">Profile</span>
                <span>{liveStatus.profile_id || "—"}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-[var(--muted)]">Elapsed</span>
                <span>{formatMinutes(liveStatus.elapsed_minutes)}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-[var(--muted)]">Topics searched</span>
                <span className="max-w-[60%] text-right">{liveStatus.topics_searched.join(", ") || "—"}</span>
              </div>
            </div>
          </Card>
          <Card className="p-6">
            <div className="section-eyebrow">Current watch</div>
            <div className="mt-2 text-lg font-semibold text-[var(--ink)]">Active playback</div>
            {liveStatus.current_watch ? (
              <div className="mt-5 space-y-3">
                <div className="text-lg font-semibold text-[var(--ink)]">
                  {liveStatus.current_watch.title}
                </div>
                <div className="text-sm text-[var(--muted)]">
                  {liveStatus.current_watch.search_keyword || "no keyword"}
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-[var(--panel-soft)]">
                  <div
                    className="h-full rounded-full bg-[linear-gradient(90deg,var(--brand),#f5a524)]"
                    style={{
                      width: liveStatus.current_watch.target_seconds
                        ? `${Math.min(
                            (liveStatus.current_watch.watched_seconds /
                              liveStatus.current_watch.target_seconds) *
                              100,
                            100,
                          )}%`
                        : "22%",
                    }}
                  />
                </div>
                <div className="text-sm text-[var(--muted)]">
                  {liveStatus.current_watch.watched_seconds.toFixed(1)}s watched
                  {liveStatus.current_watch.target_seconds
                    ? ` / ${liveStatus.current_watch.target_seconds.toFixed(1)}s target`
                    : ""}
                </div>
              </div>
            ) : (
              <div className="text-sm text-[var(--muted)]">
                Session is active, but no current video is exposed yet.
              </div>
            )}
          </Card>
        </div>
      ) : null}

      <div className="flex gap-3">
        {(["overview", ...(hasAds ? (["ads"] as const) : [])] as const).map((tab) => (
          <button
            key={tab}
            className={`rounded-2xl px-4 py-2 text-sm font-semibold transition ${
              activeTab === tab
                ? "bg-[var(--brand)] text-white"
                : "border border-[var(--line)] bg-white text-[var(--ink)]"
            }`}
            onClick={() => setActiveTab(tab)}
          >
            {tab}
          </button>
        ))}
      </div>

      {activeTab === "overview" ? (
        <div className="grid gap-6 xl:grid-cols-[1fr_1fr]">
          <Card className="p-6">
            <div className="mb-4 text-lg font-semibold text-[var(--ink)]">Topics</div>
            <div className="flex flex-wrap gap-2">
              {session.topics_searched.length > 0
                ? session.topics_searched.map((topic) => (
                    <span
                      key={topic}
                      className="rounded-full bg-slate-100 px-3 py-1 text-sm text-slate-700"
                    >
                      {topic}
                    </span>
                  ))
                : session.requested_topics.map((topic) => (
                    <span
                      key={topic}
                      className="rounded-full bg-slate-100 px-3 py-1 text-sm text-slate-700"
                    >
                      {topic}
                    </span>
                  ))}
            </div>
          </Card>
          <Card className="p-6">
            <div className="mb-4 text-lg font-semibold text-[var(--ink)]">
              Session metadata
            </div>
            <div className="space-y-3 text-sm">
              <div className="flex justify-between gap-4">
                <span className="text-[var(--muted)]">Queued</span>
                <span>{formatDate(session.queued_at)}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-[var(--muted)]">Started</span>
                <span>{formatDate(session.started_at)}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-[var(--muted)]">Finished</span>
                <span>{formatDate(session.finished_at)}</span>
              </div>
            </div>
          </Card>
          <Card className="p-0 xl:col-span-2">
            <div className="border-b border-[var(--line)] px-6 py-4 text-lg font-semibold text-[var(--ink)]">
              Watched videos
            </div>
            {(session.watched_videos ?? []).length > 0 ? (
              <div className="divide-y divide-[var(--line)]">
                {(session.watched_videos ?? []).map((video) => (
                  <div key={`${video.position}-${video.recorded_at}`} className="px-6 py-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <div className="font-semibold text-[var(--ink)]">{video.title}</div>
                        <div className="mt-1 text-sm text-[var(--muted)]">
                          {video.search_keyword || "no search keyword"} ·{" "}
                          {video.watched_seconds.toFixed(1)}s /{" "}
                          {video.target_seconds.toFixed(1)}s
                        </div>
                      </div>
                      <Badge tone={video.completed ? "success" : "warning"}>
                        {video.completed ? "completed" : "partial"}
                      </Badge>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="px-6 py-8 text-sm text-[var(--muted)]">
                No watched videos were recorded for this session yet.
              </div>
            )}
          </Card>
        </div>
      ) : null}

      {activeTab === "ads" && hasAds ? (
        <div className="space-y-4">
          {visibleAdCards.map((item) => {
            const analysisResult =
              readAnalysisField(item.primaryCapture, "result") ??
              (item.primaryCapture?.analysis_status === "completed"
                ? "relevant"
                : item.primaryCapture?.analysis_status === "not_relevant"
                  ? "not_relevant"
                  : null);
            const analysisReason = readAnalysisField(item.primaryCapture, "reason");
            const analysisCategory = readAnalysisField(item.primaryCapture, "category");
            const analysisLabel = getAnalysisLabel(
              analysisResult,
              item.primaryCapture?.analysis_status,
            );
            const mediaHiddenByAnalysis =
              analysisResult === "not_relevant" ||
              item.primaryCapture?.analysis_status === "not_relevant";
            const isExpanded = expandedAd === item.position;

            return (
              <Card key={`ad-card-${item.position}`} className="p-6">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <div className="mt-2 text-lg font-semibold text-[var(--ink)]">
                      {item.ad?.headline_text ||
                        item.primaryCapture?.headline_text ||
                        item.ad?.advertiser_domain ||
                        item.primaryCapture?.advertiser_domain ||
                        "Untitled ad"}
                    </div>
                    <div className="mt-2 text-sm text-[var(--muted)]">
                      {item.ad?.display_url ||
                        item.primaryCapture?.display_url ||
                        item.ad?.advertiser_domain ||
                        item.primaryCapture?.advertiser_domain ||
                        "unknown domain"}
                    </div>
                  </div>
                  <div className="flex flex-wrap justify-end gap-2">
                    {item.ad ? (
                      <Badge tone={item.ad.completed ? "success" : "warning"}>
                        {item.ad.completed ? "completed" : "partial"}
                      </Badge>
                    ) : null}
                    {item.primaryCapture ? (
                      <Badge
                        tone={
                          item.primaryCapture.video_status === "completed"
                            ? "success"
                            : "warning"
                        }
                      >
                        video {item.primaryCapture.video_status}
                      </Badge>
                    ) : null}
                    {item.primaryCapture ? (
                      <Badge
                        tone={
                          item.primaryCapture.landing_status === "completed"
                            ? "info"
                            : "warning"
                        }
                      >
                        landing {item.primaryCapture.landing_status}
                      </Badge>
                    ) : null}
                    {analysisResult ? (
                      <Badge tone={getAnalysisTone(analysisResult) as never}>
                        {analysisLabel}
                      </Badge>
                    ) : item.primaryCapture?.analysis_status ? (
                      <Badge tone={getAnalysisTone(null) as never}>{analysisLabel}</Badge>
                    ) : null}
                  </div>
                </div>

                <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-sm text-[var(--muted)]">
                  <div>Watched: {item.ad?.watched_seconds.toFixed(1) || "—"}s</div>
                  <div>CTA: {item.ad?.cta_text || "—"}</div>
                  <div>
                    Duration:{" "}
                    {(
                      item.ad?.ad_duration_seconds ??
                      item.primaryCapture?.ad_duration_seconds
                    )?.toFixed(1) || "—"}
                    s
                  </div>
                </div>

                <div className="mt-4 flex items-center justify-end gap-3">
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => setExpandedAd((prev) => (prev === item.position ? null : item.position))}
                  >
                    {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                    {isExpanded ? "Hide details" : "Show details"}
                  </Button>
                </div>

                {isExpanded ? (
                  <div className="mt-5 space-y-4 border-t border-[var(--line)] pt-5">
                    {item.ad?.full_text ? (
                      <div className="rounded-2xl border border-[var(--line)] bg-white px-4 py-4 text-sm text-[var(--ink)]">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-semibold text-[var(--ink)]">Ad text</span>
                          <Badge tone="info">visible text</Badge>
                        </div>
                        <div className="mt-3 whitespace-pre-wrap leading-6 text-[var(--ink)]">
                          {item.ad.full_text}
                        </div>
                      </div>
                    ) : null}

                    {item.primaryCapture?.analysis_status ? (
                      <div className="rounded-2xl border border-[var(--line)] bg-white px-4 py-4 text-sm text-[var(--ink)]">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-semibold text-[var(--ink)]">Ad analysis</span>
                          <Badge
                            tone={
                              getAnalysisTone(
                                analysisResult ??
                                  (item.primaryCapture?.analysis_status === "completed"
                                    ? "relevant"
                                    : item.primaryCapture?.analysis_status === "not_relevant"
                                      ? "not_relevant"
                                      : null),
                              ) as never
                            }
                          >
                            {analysisLabel}
                          </Badge>
                          {analysisCategory ? <Badge tone="info">{analysisCategory}</Badge> : null}
                        </div>
                        {analysisReason ? (
                          <div className="mt-3 leading-6 text-[var(--ink)]">{analysisReason}</div>
                        ) : (
                          <div className="mt-3 text-[var(--muted)]">
                            {item.primaryCapture.analysis_status === "pending"
                              ? "The ad is waiting for analysis."
                              : item.primaryCapture.analysis_status === "failed"
                                ? "The ad could not be analyzed."
                                : "Analysis is available without an explanation block."}
                          </div>
                        )}
                      </div>
                    ) : null}

                    {item.captures.length > 0 && !mediaHiddenByAnalysis ? (
                      <div className="space-y-3 rounded-2xl border border-[var(--line)] bg-white px-4 py-4 text-sm">
                        <div className="text-sm font-semibold text-[var(--ink)]">
                          Media preview
                          {item.captures.length > 1 ? ` (${item.captures.length} segments)` : ""}
                        </div>
                        {item.captures.map((capture, index) => (
                          <div
                            key={`${capture.ad_position}-${index}`}
                            className="space-y-3 rounded-2xl bg-[var(--panel-soft)] p-4"
                          >
                            {(() => {
                              const videoUrl = buildMediaUrl(capture.video_file);
                              const firstScreenshot = capture.screenshot_paths[0]?.file_path;
                              const screenshotUrl = buildMediaUrl(firstScreenshot);
                              const landingIndexUrl = capture.landing_dir
                                ? buildMediaUrl(`${capture.landing_dir}/index.html`)
                                : null;

                              return (
                                <>
                            {item.captures.length > 1 ? (
                              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--muted)]">
                                Segment {index + 1}
                              </div>
                            ) : null}

                            <div className="grid gap-3 lg:grid-cols-3">
                              <div className="relative overflow-hidden rounded-2xl bg-slate-950 p-4 text-white min-h-40">
                                <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,_rgba(248,113,113,0.28),_transparent_55%)]" />
                                <div className="relative flex h-full flex-col justify-between">
                                  <div className="flex items-center justify-between">
                                    <Badge tone={capture.video_status === "completed" ? "success" : "warning"}>
                                      {capture.video_status === "completed" ? "video saved" : "video pending"}
                                    </Badge>
                                    <Film size={18} className="text-white/70" />
                                  </div>
                                  {videoUrl ? (
                                    <div className="py-4">
                                      <video
                                        key={videoUrl}
                                        className="h-36 w-full rounded-xl bg-black object-cover"
                                        controls
                                        preload="metadata"
                                        src={videoUrl}
                                        onLoadedMetadata={(event) => {
                                          try {
                                            event.currentTarget.currentTime = 0;
                                          } catch {
                                            // Ignore browser seek restrictions for read-only preview.
                                          }
                                        }}
                                      />
                                    </div>
                                  ) : (
                                    <div className="flex items-center justify-center py-5">
                                      <div className="flex h-16 w-16 items-center justify-center rounded-full bg-white/10 backdrop-blur-sm">
                                        <PlayCircle size={34} className="text-white" />
                                      </div>
                                    </div>
                                  )}
                                  <div>
                                    <div className="text-sm font-semibold">Ad video</div>
                                    <div className="mt-1 truncate text-xs text-white/70">
                                      {getBaseName(capture.video_file) || "video.webm"}
                                    </div>
                                    {videoUrl ? (
                                      <a
                                        href={videoUrl}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="mt-2 inline-flex text-xs font-semibold text-white/85 underline underline-offset-4 hover:text-white"
                                      >
                                        Open video
                                      </a>
                                    ) : null}
                                  </div>
                                </div>
                              </div>

                              <div className="rounded-2xl border border-[var(--line)] bg-white p-4 min-h-40">
                                <div className="flex items-center justify-between">
                                  <Badge tone={capture.screenshot_paths.length > 0 ? "info" : "neutral"}>
                                    {capture.screenshot_paths.length} screenshots
                                  </Badge>
                                  <FileImage size={18} className="text-[var(--muted)]" />
                                </div>
                                <div className="mt-4">
                                  {screenshotUrl ? (
                                    <a href={screenshotUrl} target="_blank" rel="noreferrer">
                                      <img
                                        src={screenshotUrl}
                                        alt="Ad screenshot preview"
                                        className="h-28 w-full rounded-xl border border-slate-200 object-cover"
                                      />
                                    </a>
                                  ) : (
                                    <div className="flex items-end gap-3">
                                      {[0, 1, 2].map((layer) => (
                                        <div
                                          key={layer}
                                          className={`rounded-xl border border-slate-200 bg-gradient-to-br from-slate-100 to-slate-200 ${
                                            layer === 0
                                              ? "h-24 w-20"
                                              : layer === 1
                                                ? "h-20 w-16"
                                                : "h-16 w-12"
                                          }`}
                                        />
                                      ))}
                                    </div>
                                  )}
                                </div>
                                <div className="mt-4 text-sm font-semibold text-[var(--ink)]">
                                  Screenshot timeline
                                </div>
                                <div className="mt-1 flex items-center justify-between gap-3">
                                  <div className="text-xs text-[var(--muted)]">
                                    Fallback frames and sampled moments from the ad.
                                  </div>
                                  {screenshotUrl ? (
                                    <a
                                      href={screenshotUrl}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="shrink-0 text-xs font-semibold text-[var(--brand)] underline underline-offset-4"
                                    >
                                      Open screenshot
                                    </a>
                                  ) : null}
                                </div>
                              </div>

                              <div className="rounded-2xl border border-[var(--line)] bg-white p-4 min-h-40">
                                <div className="flex items-center justify-between">
                                  <Badge tone={capture.landing_status === "completed" ? "info" : "warning"}>
                                    {capture.landing_status === "completed" ? "landing saved" : "landing pending"}
                                  </Badge>
                                  <Globe size={18} className="text-[var(--muted)]" />
                                </div>
                                <div className="mt-5 overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
                                  <div className="flex items-center gap-1.5 border-b border-slate-200 px-3 py-2">
                                    <span className="h-2.5 w-2.5 rounded-full bg-rose-300" />
                                    <span className="h-2.5 w-2.5 rounded-full bg-amber-300" />
                                    <span className="h-2.5 w-2.5 rounded-full bg-emerald-300" />
                                  </div>
                                  <div className="px-3 py-4">
                                    <div className="flex items-center gap-2 text-sm font-semibold text-[var(--ink)]">
                                      <FolderOpen size={15} />
                                      {getLandingHost(capture.landing_url) || "landing page"}
                                    </div>
                                    <div className="mt-2 truncate text-xs text-[var(--muted)]">
                                      {getBaseName(capture.landing_dir) || "landing/"}
                                    </div>
                                  </div>
                                </div>
                                <div className="mt-4 text-xs text-[var(--muted)]">
                                  Saved HTML and assets snapshot.
                                </div>
                                {landingIndexUrl ? (
                                  <a
                                    href={landingIndexUrl}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="mt-3 inline-flex text-xs font-semibold text-[var(--brand)] underline underline-offset-4"
                                  >
                                    Open landing snapshot
                                  </a>
                                ) : null}
                              </div>
                            </div>
                                </>
                              );
                            })()}
                          </div>
                        ))}
                      </div>
                    ) : null}

                  </div>
                ) : null}
              </Card>
            );
          })}
          {adCards.length > 4 ? (
            <div className="flex justify-center">
              <Button
                type="button"
                variant="ghost"
                onClick={() => setShowAllAds((prev) => !prev)}
              >
                {showAllAds ? "Show fewer ads" : `Show all ads (${adCards.length})`}
              </Button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
