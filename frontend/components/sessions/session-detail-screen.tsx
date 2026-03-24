import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
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
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link to="/sessions" className="inline-flex items-center gap-1.5 text-sm text-[var(--brand)] transition hover:text-[var(--brand-strong)]">
            <ArrowLeft size={14} />
            Sessions
          </Link>
          <h2 className="mt-2 text-lg font-semibold text-[var(--ink)]">
            Session {session.session_id.slice(0, 12)}
          </h2>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Badge tone={getStatusTone(session.status) as never}>{session.status}</Badge>
            <span className="text-xs text-[var(--muted)]">
              {formatDate(session.queued_at)}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
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

      <div className="metric-grid">
        <Card>
          <div className="text-xs font-medium uppercase tracking-wider text-[var(--muted)]">Requested</div>
          <div className="mt-2 text-2xl font-semibold text-[var(--ink)]">{session.requested_duration_minutes}m</div>
          <div className="mt-1 text-xs text-[var(--muted)]">Actual {formatMinutes(session.elapsed_minutes)}</div>
        </Card>
        <Card>
          <div className="text-xs font-medium uppercase tracking-wider text-[var(--muted)]">Videos</div>
          <div className="mt-2 text-2xl font-semibold text-[var(--ink)]">{formatNumber(session.watched_videos_count)}</div>
          <div className="mt-1 text-xs text-[var(--muted)]">{formatNumber(session.videos_watched)} completed</div>
        </Card>
        <Card>
          <div className="text-xs font-medium uppercase tracking-wider text-[var(--muted)]">Ads</div>
          <div className="mt-2 text-2xl font-semibold text-[var(--ink)]">{formatNumber(session.watched_ads_count)}</div>
          <div className="mt-1 text-xs text-[var(--muted)]">{session.captures.video_captures} video captures</div>
        </Card>
        <Card>
          <div className="text-xs font-medium uppercase tracking-wider text-[var(--muted)]">Traffic</div>
          <div className="mt-2 text-2xl font-semibold text-[var(--ink)]">{formatBytes(session.bytes_downloaded)}</div>
          <div className="mt-1 text-xs text-[var(--muted)]">downloaded</div>
        </Card>
      </div>

      {liveStatus ? (
        <div className="grid gap-4 xl:grid-cols-2">
          <Card className="p-5" glow>
            <div className="mb-3 flex items-center gap-2">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--accent)] opacity-75" />
                <span className="inline-flex h-2 w-2 rounded-full bg-[var(--accent)]" />
              </span>
              <span className="text-sm font-semibold text-[var(--ink)]">Live runtime</span>
            </div>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between gap-4">
                <span className="text-[var(--muted)]">Profile</span>
                <span className="text-[var(--ink-secondary)]">{liveStatus.profile_id || "—"}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-[var(--muted)]">Elapsed</span>
                <span className="text-[var(--ink-secondary)]">{formatMinutes(liveStatus.elapsed_minutes)}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-[var(--muted)]">Topics searched</span>
                <span className="text-[var(--ink-secondary)]">{liveStatus.topics_searched.join(", ") || "—"}</span>
              </div>
            </div>
          </Card>
          <Card className="p-5">
            <div className="mb-3 text-sm font-semibold text-[var(--ink)]">Current watch</div>
            {liveStatus.current_watch ? (
              <div className="space-y-2">
                <div className="text-base font-semibold text-[var(--ink)]">
                  {liveStatus.current_watch.title}
                </div>
                <div className="text-sm text-[var(--muted)]">
                  {liveStatus.current_watch.search_keyword || "no keyword"}
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

      <div className="flex gap-1.5">
        {(["overview", ...(hasAds ? (["ads"] as const) : [])] as const).map((tab) => (
          <button
            key={tab}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium capitalize transition-all ${
              activeTab === tab
                ? "bg-[var(--brand-soft)] text-[var(--brand)]"
                : "text-[var(--muted)] hover:bg-[var(--panel-hover)] hover:text-[var(--ink)]"
            }`}
            onClick={() => setActiveTab(tab)}
          >
            {tab}
          </button>
        ))}
      </div>

      {activeTab === "overview" ? (
        <div className="grid gap-4 xl:grid-cols-2">
          <Card className="p-5">
            <div className="mb-3 text-sm font-semibold text-[var(--ink)]">Topics</div>
            <div className="flex flex-wrap gap-1.5">
              {(session.topics_searched.length > 0
                ? session.topics_searched
                : session.requested_topics
              ).map((topic) => (
                <span
                  key={topic}
                  className="rounded-md border border-[var(--line)] bg-[var(--panel)] px-2.5 py-1 text-xs text-[var(--ink-secondary)]"
                >
                  {topic}
                </span>
              ))}
            </div>
          </Card>
          <Card className="p-5">
            <div className="mb-3 text-sm font-semibold text-[var(--ink)]">Session metadata</div>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between gap-4">
                <span className="text-[var(--muted)]">Queued</span>
                <span className="text-[var(--ink-secondary)]">{formatDate(session.queued_at)}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-[var(--muted)]">Started</span>
                <span className="text-[var(--ink-secondary)]">{formatDate(session.started_at)}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-[var(--muted)]">Finished</span>
                <span className="text-[var(--ink-secondary)]">{formatDate(session.finished_at)}</span>
              </div>
            </div>
          </Card>
          <Card className="p-0 xl:col-span-2">
            <div className="border-b border-[var(--line)] px-5 py-4 text-sm font-semibold text-[var(--ink)]">
              Watched videos
            </div>
            {(session.watched_videos ?? []).length > 0 ? (
              <div className="divide-y divide-[var(--line)]">
                {(session.watched_videos ?? []).map((video) => (
                  <div key={`${video.position}-${video.recorded_at}`} className="px-5 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <div className="text-sm font-medium text-[var(--ink)]">{video.title}</div>
                        <div className="mt-0.5 text-xs text-[var(--muted)]">
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
              <div className="px-5 py-8 text-sm text-[var(--muted)]">
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
              <Card key={`ad-card-${item.position}`} className="p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-semibold text-[var(--ink)]">
                      {item.ad?.headline_text ||
                        item.primaryCapture?.headline_text ||
                        item.ad?.advertiser_domain ||
                        item.primaryCapture?.advertiser_domain ||
                        "Untitled ad"}
                    </div>
                    <div className="mt-1 text-xs text-[var(--muted)]">
                      {item.ad?.display_url ||
                        item.primaryCapture?.display_url ||
                        item.ad?.advertiser_domain ||
                        item.primaryCapture?.advertiser_domain ||
                        "unknown domain"}
                    </div>
                  </div>
                  <div className="flex flex-wrap justify-end gap-1.5">
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

                <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-[var(--muted)]">
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

                <div className="mt-3 flex items-center justify-end">
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => setExpandedAd((prev) => (prev === item.position ? null : item.position))}
                    className="gap-1.5 px-2.5 py-1.5 text-xs"
                  >
                    {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    {isExpanded ? "Hide details" : "Show details"}
                  </Button>
                </div>

                {isExpanded ? (
                  <div className="mt-4 space-y-3 border-t border-[var(--line)] pt-4">
                    {item.ad?.full_text ? (
                      <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] p-4 text-sm">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-semibold text-[var(--ink)]">Ad text</span>
                          <Badge tone="info">visible text</Badge>
                        </div>
                        <div className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-[var(--ink-secondary)]">
                          {item.ad.full_text}
                        </div>
                      </div>
                    ) : null}

                    {item.primaryCapture?.analysis_status ? (
                      <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] p-4 text-sm">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-xs font-semibold text-[var(--ink)]">Ad analysis</span>
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
                          <div className="mt-2 text-sm leading-relaxed text-[var(--ink-secondary)]">{analysisReason}</div>
                        ) : (
                          <div className="mt-2 text-[var(--muted)]">
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
                      <div className="space-y-3 rounded-lg border border-[var(--line)] bg-[var(--panel)] p-4 text-sm">
                        <div className="text-xs font-semibold text-[var(--ink)]">
                          Media preview
                          {item.captures.length > 1 ? ` (${item.captures.length} segments)` : ""}
                        </div>
                        {item.captures.map((capture, index) => (
                          <div
                            key={`${capture.ad_position}-${index}`}
                            className="space-y-3 rounded-lg bg-[var(--bg-soft)] p-4"
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
                              <div className="text-xs font-medium uppercase tracking-wider text-[var(--muted)]">
                                Segment {index + 1}
                              </div>
                            ) : null}

                            <div className="grid gap-3 lg:grid-cols-3">
                              <div className="relative overflow-hidden rounded-lg bg-[var(--panel-soft)] p-4 min-h-36">
                                <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,_rgba(108,92,231,0.06),_transparent_55%)]" />
                                <div className="relative flex h-full flex-col justify-between">
                                  <div className="flex items-center justify-between">
                                    <Badge tone={capture.video_status === "completed" ? "success" : "warning"}>
                                      {capture.video_status === "completed" ? "video saved" : "video pending"}
                                    </Badge>
                                    <Film size={15} className="text-[var(--muted)]" />
                                  </div>
                                  {videoUrl ? (
                                    <div className="py-3">
                                      <video
                                        key={videoUrl}
                                        className="h-32 w-full rounded-lg bg-slate-900 object-cover"
                                        controls
                                        preload="metadata"
                                        src={videoUrl}
                                        onLoadedMetadata={(event) => {
                                          try {
                                            event.currentTarget.currentTime = 0;
                                          } catch {
                                            // Ignore browser seek restrictions
                                          }
                                        }}
                                      />
                                    </div>
                                  ) : (
                                    <div className="flex items-center justify-center py-4">
                                      <div className="flex h-14 w-14 items-center justify-center rounded-full bg-[var(--brand-soft)]">
                                        <PlayCircle size={28} className="text-[var(--brand)]" />
                                      </div>
                                    </div>
                                  )}
                                  <div>
                                    <div className="text-xs font-semibold text-[var(--ink)]">Ad video</div>
                                    <div className="mt-0.5 truncate text-xs text-[var(--muted)]">
                                      {getBaseName(capture.video_file) || "video.webm"}
                                    </div>
                                    {videoUrl ? (
                                      <a
                                        href={videoUrl}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="mt-1.5 inline-flex text-xs font-medium text-[var(--brand)] hover:underline"
                                      >
                                        Open video
                                      </a>
                                    ) : null}
                                  </div>
                                </div>
                              </div>

                              <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] p-4 min-h-36">
                                <div className="flex items-center justify-between">
                                  <Badge tone={capture.screenshot_paths.length > 0 ? "info" : "neutral"}>
                                    {capture.screenshot_paths.length} screenshots
                                  </Badge>
                                  <FileImage size={15} className="text-[var(--muted)]" />
                                </div>
                                <div className="mt-3">
                                  {screenshotUrl ? (
                                    <a href={screenshotUrl} target="_blank" rel="noreferrer">
                                      <img
                                        src={screenshotUrl}
                                        alt="Ad screenshot preview"
                                        className="h-24 w-full rounded-lg border border-[var(--line)] object-cover"
                                      />
                                    </a>
                                  ) : (
                                    <div className="flex items-end gap-2">
                                      {[0, 1, 2].map((layer) => (
                                        <div
                                          key={layer}
                                          className={`rounded-lg border border-[var(--line)] bg-[var(--bg-soft)] ${
                                            layer === 0
                                              ? "h-20 w-16"
                                              : layer === 1
                                                ? "h-16 w-12"
                                                : "h-12 w-10"
                                          }`}
                                        />
                                      ))}
                                    </div>
                                  )}
                                </div>
                                <div className="mt-3 text-xs font-semibold text-[var(--ink)]">
                                  Screenshot timeline
                                </div>
                                <div className="mt-0.5 flex items-center justify-between gap-2">
                                  <div className="text-xs text-[var(--muted)]">
                                    Fallback frames from the ad.
                                  </div>
                                  {screenshotUrl ? (
                                    <a
                                      href={screenshotUrl}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="shrink-0 text-xs font-medium text-[var(--brand)] hover:underline"
                                    >
                                      Open
                                    </a>
                                  ) : null}
                                </div>
                              </div>

                              <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] p-4 min-h-36">
                                <div className="flex items-center justify-between">
                                  <Badge tone={capture.landing_status === "completed" ? "info" : "warning"}>
                                    {capture.landing_status === "completed" ? "landing saved" : "landing pending"}
                                  </Badge>
                                  <Globe size={15} className="text-[var(--muted)]" />
                                </div>
                                <div className="mt-4 overflow-hidden rounded-lg border border-[var(--line)] bg-[var(--bg-soft)]">
                                  <div className="flex items-center gap-1.5 border-b border-[var(--line)] px-3 py-1.5">
                                    <span className="h-2 w-2 rounded-full bg-[var(--danger)]" />
                                    <span className="h-2 w-2 rounded-full bg-[var(--warning)]" />
                                    <span className="h-2 w-2 rounded-full bg-[var(--accent)]" />
                                  </div>
                                  <div className="px-3 py-3">
                                    <div className="flex items-center gap-2 text-xs font-semibold text-[var(--ink)]">
                                      <FolderOpen size={13} />
                                      {getLandingHost(capture.landing_url) || "landing page"}
                                    </div>
                                    <div className="mt-1 truncate text-xs text-[var(--muted)]">
                                      {getBaseName(capture.landing_dir) || "landing/"}
                                    </div>
                                  </div>
                                </div>
                                <div className="mt-3 text-xs text-[var(--muted)]">
                                  Saved HTML snapshot.
                                </div>
                                {landingIndexUrl ? (
                                  <a
                                    href={landingIndexUrl}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="mt-2 inline-flex text-xs font-medium text-[var(--brand)] hover:underline"
                                  >
                                    Open landing
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
