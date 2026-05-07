import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Clock,
  Download,
  ExternalLink,
  Globe,
  Image as ImageIcon,
  LayoutPanelTop,
  Megaphone,
  MousePointerClick,
  Play,
  Sparkles,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Loader } from "@/components/ui/loader";
import { apiClient } from "@/lib/api-client";
import {
  getEmulationDetail,
  getEmulationStatus,
  resumeEmulation,
  retryEmulation,
  streamEmulationStatus,
  stopEmulation,
} from "@/lib/api";
import { formatBytes, formatDate, formatMinutes, formatNumber } from "@/lib/format";
import { formatSessionStatus, getStatusTone } from "@/lib/metrics";
import type {
  EmulationAdCapture,
  EmulationHistoryDetail,
  EmulationSessionStatus,
  EmulationWatchedAd,
  EmulationWatchedVideo,
} from "@/types/api";

type AdCard = {
  position: number;
  ad: EmulationWatchedAd | null;
  captures: EmulationAdCapture[];
  primaryCapture: EmulationAdCapture | null;
};

type SessionVideoItem = EmulationWatchedVideo & {
  runtime?: boolean;
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

function resolveAnalysisResult(capture: EmulationAdCapture | null) {
  const result = readAnalysisField(capture, "result");
  if (result === "relevant" || result === "not_relevant") {
    return result;
  }
  if (capture?.analysis_status === "completed") {
    return "relevant";
  }
  if (capture?.analysis_status === "not_relevant") {
    return "not_relevant";
  }
  return null;
}

function getAnalysisLabel(result: string | null, status?: string | null) {
  if (result === "relevant") {
    return "Релевантно";
  }
  if (result === "not_relevant") {
    return "Не релевантно";
  }
  if (status === "failed") {
    return "Ошибка анализа";
  }
  if (status === "pending") {
    return "Анализ ожидает";
  }
  return "Нет анализа";
}

function formatWatchTiming(
  watchedSeconds: number | null | undefined,
  targetSeconds: number | null | undefined,
) {
  const watched = typeof watchedSeconds === "number" && Number.isFinite(watchedSeconds)
    ? watchedSeconds
    : 0;
  const target = typeof targetSeconds === "number" && Number.isFinite(targetSeconds)
    ? targetSeconds
    : null;

  if (target === null) {
    return `${watched.toFixed(1)}с факт`;
  }

  return `${watched.toFixed(1)}с факт / ${target.toFixed(1)}с план`;
}

function formatCaptureStatus(status: string | null | undefined) {
  switch (status) {
    case "completed":
      return "завершено";
    case "pending":
      return "ожидается";
    case "failed":
      return "ошибка";
    case "skipped":
      return "пропущено";
    case "fallback_screenshots":
      return "скриншоты";
    case "no_src":
      return "без источника";
    default:
      return status ?? "—";
  }
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

function isGoogleAdClickHost(value: string | null | undefined) {
  const host = getLandingHost(value)?.toLowerCase() ?? "";
  return host === "googleadservices.com" || host === "www.googleadservices.com";
}

function isSubscriptionHeadline(value: string | null | undefined) {
  return /^(subscribe to|подпишитесь на|подписаться на)/i.test((value ?? "").trim());
}

function getAdLandingHost(
  ad: EmulationWatchedAd | null,
  capture: EmulationAdCapture | null,
) {
  return (
    getLandingHost(capture?.landing_url) ||
    getLandingHost(ad?.landing_urls?.[0]) ||
    getLandingHost(ad?.advertiser_domain) ||
    getLandingHost(capture?.advertiser_domain) ||
    (!isGoogleAdClickHost(ad?.display_url) ? getLandingHost(ad?.display_url) : null) ||
    (!isGoogleAdClickHost(capture?.display_url) ? getLandingHost(capture?.display_url) : null)
  );
}

function getAdCardTitle(
  ad: EmulationWatchedAd | null,
  capture: EmulationAdCapture | null,
) {
  const headline = pickFirstString(ad?.headline_text, capture?.headline_text);
  if (headline && !isSubscriptionHeadline(headline)) {
    return headline;
  }
  return (
    getAdLandingHost(ad, capture) ||
    pickFirstString(ad?.advertiser_domain, capture?.advertiser_domain) ||
    "Реклама без названия"
  );
}

function getPostProcessingLabel(
  status: string | null | undefined,
  sessionStatus: string | null | undefined,
  watchedAdsCount: number,
  progress?: { done: number; total: number } | null,
) {
  if (!status) {
    if (
      sessionStatus === "completed" ||
      sessionStatus === "failed" ||
      sessionStatus === "stopped"
    ) {
      if (watchedAdsCount === 0) {
        return "Анализ рекламы не требуется";
      }
      return "Состояние анализа рекламы недоступно";
    }
    return null;
  }

  if (status === "queued") {
    if (progress && progress.total > 0) {
      return `Анализ рекламы в очереди (${progress.done}/${progress.total})`;
    }
    return "Анализ рекламы в очереди";
  }
  if (status === "running") {
    if (progress && progress.total > 0) {
      return `Анализ рекламы выполняется (${progress.done}/${progress.total})`;
    }
    return "Анализ рекламы выполняется";
  }
  if (status === "completed") {
    if (progress && progress.total > 0) {
      return `Анализ рекламы завершен (${progress.done}/${progress.total})`;
    }
    return "Анализ рекламы завершен";
  }
  if (status === "failed") {
    return "Анализ рекламы завершился с ошибками";
  }
  return null;
}

function buildMediaPath(value: string | null | undefined) {
  if (!value) {
    return null;
  }
  const normalized = value.replace(/\\/g, "/").replace(/^\.\//, "");
  const isAbsolute = normalized.startsWith("/");
  const encoded = normalized
    .split("/")
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  const prefix = isAbsolute ? "/" : "";
  return `/emulation/media/${prefix}${encoded}`;
}

async function downloadProtectedMedia(
  value: string | null | undefined,
  filename: string,
) {
  const mediaPath = buildMediaPath(value);
  if (!mediaPath) {
    return;
  }

  try {
    const response = await apiClient.get<Blob>(mediaPath, { responseType: "blob" });
    const objectUrl = URL.createObjectURL(response.data);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
  } catch (error) {
    console.error("Не удалось скачать защищенный медиафайл", error);
  }
}

function useProtectedMediaBlobUrl(value: string | null | undefined) {
  const mediaPath = useMemo(() => buildMediaPath(value), [value]);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!mediaPath) {
      setBlobUrl(null);
      return;
    }

    let active = true;
    let objectUrl: string | null = null;

    void apiClient
      .get<Blob>(mediaPath, { responseType: "blob" })
      .then((response) => {
        if (!active) {
          return;
        }
        objectUrl = URL.createObjectURL(response.data);
        setBlobUrl(objectUrl);
      })
      .catch(() => {
        if (active) {
          setBlobUrl(null);
        }
      });

    return () => {
      active = false;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [mediaPath]);

  return blobUrl;
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
    analysis_status: capture.analysis_status ?? "pending",
    analysis_summary: capture.analysis_summary ?? null,
    screenshot_paths: capture.screenshot_paths ?? [],
  };
}

function pickFirstString(
  ...values: Array<string | null | undefined>
): string | null {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value;
    }
  }
  return null;
}

function pickFirstNumber(
  ...values: Array<number | null | undefined>
): number | null {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
  }
  return null;
}

function isTerminalAnalysisStatus(status: string | null | undefined) {
  return (
    status === "completed" ||
    status === "not_relevant" ||
    status === "failed" ||
    status === "skipped"
  );
}

function mergeCapture(
  preferred: EmulationAdCapture | null,
  fallback: EmulationAdCapture | null,
): EmulationAdCapture | null {
  if (!preferred) {
    return fallback;
  }
  if (!fallback) {
    return preferred;
  }

  const videoFile = pickFirstString(preferred.video_file, fallback.video_file);
  const landingDir = pickFirstString(preferred.landing_dir, fallback.landing_dir);
  const preferredHasTerminalAnalysis =
    preferred.analysis_summary != null || isTerminalAnalysisStatus(preferred.analysis_status);
  const fallbackHasTerminalAnalysis =
    fallback.analysis_summary != null || isTerminalAnalysisStatus(fallback.analysis_status);

  return {
    ...fallback,
    ...preferred,
    advertiser_domain: pickFirstString(
      preferred.advertiser_domain,
      fallback.advertiser_domain,
    ),
    cta_href: pickFirstString(preferred.cta_href, fallback.cta_href),
    display_url: pickFirstString(preferred.display_url, fallback.display_url),
    headline_text: pickFirstString(preferred.headline_text, fallback.headline_text),
    ad_duration_seconds: pickFirstNumber(
      preferred.ad_duration_seconds,
      fallback.ad_duration_seconds,
    ),
    landing_url: pickFirstString(preferred.landing_url, fallback.landing_url),
    landing_dir: landingDir,
    landing_status:
      (landingDir === preferred.landing_dir
        ? preferred.landing_status
        : landingDir === fallback.landing_dir
          ? fallback.landing_status
          : pickFirstString(preferred.landing_status, fallback.landing_status)) ?? "pending",
    video_src_url: pickFirstString(preferred.video_src_url, fallback.video_src_url),
    video_file: videoFile,
    video_status:
      (videoFile === preferred.video_file
        ? preferred.video_status
        : videoFile === fallback.video_file
          ? fallback.video_status
          : pickFirstString(preferred.video_status, fallback.video_status)) ?? "pending",
    analysis_status:
      (preferredHasTerminalAnalysis
        ? preferred.analysis_status
        : fallbackHasTerminalAnalysis
          ? fallback.analysis_status
          : pickFirstString(preferred.analysis_status, fallback.analysis_status)) ?? null,
    analysis_summary:
      preferred.analysis_summary ?? fallback.analysis_summary ?? null,
    screenshot_paths:
      preferred.screenshot_paths.length > 0
        ? preferred.screenshot_paths
        : fallback.screenshot_paths,
  };
}

function CaptureMediaPreview({
  capture,
  index,
  totalSegments,
}: {
  capture: EmulationAdCapture;
  index: number;
  totalSegments: number;
}) {
  const videoUrl = useProtectedMediaBlobUrl(capture.video_file);
  const [shotIndex, setShotIndex] = useState(0);
  const [activeTab, setActiveTab] = useState<"screens" | "video" | "landing" | null>(null);
  const total = capture.screenshot_paths.length;
  const activeScreenshot = capture.screenshot_paths[shotIndex]?.file_path;
  const screenshotUrl = useProtectedMediaBlobUrl(activeScreenshot);
  const landingFileName = `${getBaseName(capture.landing_dir) || "landing"}.html`;
  const landingCompleted = capture.landing_status === "completed";
  const canOpenLanding = Boolean(capture.landing_url && landingCompleted);
  const canDownloadLanding = Boolean(capture.landing_dir && capture.landing_status === "completed");
  const hasLandingArtifact = Boolean(canOpenLanding || canDownloadLanding);
  const hasScreenshots = total > 0;
  const hasVideo = Boolean(videoUrl);
  const landingHost = getLandingHost(capture.landing_url) || getLandingHost(capture.landing_dir) || "—";

  const toggleTab = (t: "screens" | "video" | "landing") =>
    setActiveTab((cur) => (cur === t ? null : t));

  const goPrev = () => setShotIndex((i) => Math.max(0, i - 1));
  const goNext = () => setShotIndex((i) => Math.min(total - 1, i + 1));

  type TabId = "screens" | "video" | "landing";
  function TabPill({
    id,
    icon,
    label,
    sub,
    disabled = false,
  }: {
    id: TabId;
    icon: React.ReactNode;
    label: string;
    sub: string;
    disabled?: boolean;
  }) {
    const active = activeTab === id;
    return (
      <button
        type="button"
        disabled={disabled}
        onClick={() => !disabled && toggleTab(id)}
        aria-expanded={active}
        className={`flex items-center gap-2.5 rounded-xl border px-2.5 py-2 text-left transition ${
          active
            ? "border-[var(--ink)] bg-[var(--panel)] shadow-sm"
            : disabled
              ? "border-dashed border-[var(--line)] bg-transparent opacity-50 cursor-not-allowed"
              : "border-[var(--line)] bg-[var(--panel)] hover:border-[var(--line-strong)] hover:shadow-sm"
        }`}
      >
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-[var(--bg-soft)] text-[var(--ink-secondary)]">
          {icon}
        </span>
        <span className="flex min-w-0 flex-col">
          <span className="text-[12px] font-semibold leading-tight text-[var(--ink)]">{label}</span>
          <span className="text-[10px] uppercase tracking-wider text-[var(--muted)]">{sub}</span>
        </span>
        <ChevronDown
          size={13}
          className="ml-1 shrink-0 text-[var(--muted)] transition-transform"
          style={{ transform: active ? "rotate(180deg)" : "rotate(0deg)" }}
        />
      </button>
    );
  }

  return (
    <div className="space-y-3">
      {totalSegments > 1 ? (
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--muted)]">
            сегмент {String(index + 1).padStart(2, "0")} / {String(totalSegments).padStart(2, "0")}
          </span>
          <span className="h-px flex-1 bg-[var(--line)]" />
        </div>
      ) : null}

      {/* Three tab pills */}
      <div className="flex flex-wrap gap-2">
        <TabPill
          id="screens"
          icon={<ImageIcon size={15} />}
          label="Скриншоты"
          sub={hasScreenshots ? `${total} кадра` : "нет кадров"}
          disabled={!hasScreenshots}
        />
        <TabPill
          id="video"
          icon={<Play size={15} />}
          label="Видео"
          sub={hasVideo ? "готово" : "нет видео"}
          disabled={!hasVideo}
        />
        <TabPill
          id="landing"
          icon={<Globe size={15} />}
          label="Лендинг"
          sub={hasLandingArtifact ? (landingHost !== "—" ? landingHost : "готово") : "ожидается"}
        />
      </div>

      {/* Screenshot panel */}
      {activeTab === "screens" && hasScreenshots ? (
        <div className="group relative">
          <a href={screenshotUrl ?? "#"} target="_blank" rel="noreferrer" className="block">
            <img
              src={screenshotUrl ?? ""}
              alt={`Скриншот ${shotIndex + 1} из ${total}`}
              className="aspect-[16/10] w-full rounded-xl border border-[var(--line)] object-contain"
            />
          </a>
          <div className="pointer-events-none absolute left-3 top-3 rounded-full bg-black/55 px-2.5 py-1 font-mono text-[11px] font-medium text-white backdrop-blur-sm">
            {shotIndex + 1} / {total}
          </div>
          <a
            href={screenshotUrl ?? "#"}
            target="_blank"
            rel="noreferrer"
            className="absolute right-3 top-3 inline-flex items-center gap-1.5 rounded-full bg-black/55 px-2.5 py-1 text-[11px] font-medium text-white opacity-0 backdrop-blur-sm transition group-hover:opacity-100 hover:bg-black/70"
          >
            <ExternalLink size={12} /> открыть
          </a>
          {total > 1 ? (
            <>
              <button
                type="button"
                onClick={goPrev}
                disabled={shotIndex === 0}
                aria-label="Предыдущий скриншот"
                className="absolute left-3 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-full bg-white/90 text-[var(--ink)] shadow-md transition hover:bg-white disabled:opacity-40"
              >
                <ChevronLeft size={18} />
              </button>
              <button
                type="button"
                onClick={goNext}
                disabled={shotIndex === total - 1}
                aria-label="Следующий скриншот"
                className="absolute right-3 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-full bg-white/90 text-[var(--ink)] shadow-md transition hover:bg-white disabled:opacity-40"
              >
                <ChevronRight size={18} />
              </button>
              <div className="absolute inset-x-0 bottom-3 flex justify-center gap-1.5">
                {Array.from({ length: total }).map((_, i) => (
                  <button
                    key={i}
                    type="button"
                    onClick={() => setShotIndex(i)}
                    aria-label={`Скриншот ${i + 1}`}
                    className="h-1.5 rounded-full transition-all"
                    style={{
                      width: i === shotIndex ? 18 : 6,
                      background: i === shotIndex ? "rgba(255,255,255,0.95)" : "rgba(255,255,255,0.45)",
                    }}
                  />
                ))}
              </div>
            </>
          ) : null}
        </div>
      ) : null}

      {/* Video panel */}
      {activeTab === "video" && hasVideo ? (
        <video
          key={videoUrl ?? ""}
          src={videoUrl ?? ""}
          controls
          className="aspect-video w-full rounded-xl bg-slate-900 object-contain"
          onLoadedMetadata={(e) => { try { e.currentTarget.currentTime = 0; } catch { /* ignore */ } }}
        />
      ) : null}

      {/* Landing panel */}
      {activeTab === "landing" ? (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-[var(--line)] bg-[var(--panel-soft)] px-4 py-3">
          <div className="grid h-8 w-8 place-items-center rounded-lg bg-[var(--panel)] text-[var(--ink-secondary)] ring-1 ring-[var(--line)]">
            <Globe size={15} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-[11px] font-medium uppercase tracking-wider text-[var(--muted)]">лендинг</div>
            <div className="truncate font-mono text-sm text-[var(--ink)]">{landingHost}</div>
          </div>
          {hasLandingArtifact ? (
            <div className="flex items-center gap-2">
              {canOpenLanding ? (
                <a
                  href={capture.landing_url ?? undefined}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-[var(--brand)] transition hover:bg-[var(--brand-soft)]"
                >
                  <ExternalLink size={13} /> Открыть лендинг
                </a>
              ) : null}
              {canDownloadLanding ? (
                <button
                  type="button"
                  onClick={() => void downloadProtectedMedia(
                    capture.landing_dir ? `${capture.landing_dir}/index.html` : null,
                    landingFileName,
                  )}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--brand)] px-3 py-1.5 text-xs font-medium text-white transition hover:bg-[var(--brand-strong)]"
                >
                  <Download size={13} /> Скачать HTML
                </button>
              ) : null}
            </div>
          ) : (
            <Badge tone="warning">лендинг ожидается</Badge>
          )}
        </div>
      ) : null}
    </div>
  );
}

function shouldTrackLiveStatus(
  status: string | null | undefined,
  postProcessingStatus: string | null | undefined,
) {
  return (
    status === "queued" ||
    status === "running" ||
    status === "stopping" ||
    postProcessingStatus === "queued" ||
    postProcessingStatus === "running"
  );
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
  const [adFilter, setAdFilter] = useState<"all" | "relevant" | "not_relevant" | "pending">("all");
  const [adSort, setAdSort] = useState<"position" | "duration">("position");

  useEffect(() => {
    let active = true;

    async function loadDetail() {
      try {
        const data = await getEmulationDetail(sessionId);
        if (!active) {
          return;
        }
        setError(null);
        setDetail(data);
      } catch (err) {
        if (active) {
          setError("Не удалось загрузить детали сессии.");
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void loadDetail();
    return () => {
      active = false;
    };
  }, [sessionId]);

  useEffect(() => {
    if (!detail) {
      return;
    }

    if (!shouldTrackLiveStatus(detail.status, detail.post_processing_status ?? null)) {
      setLiveStatus(null);
      return;
    }

    let active = true;
    let fallbackTimer: number | undefined;
    let streamClosedGracefully = false;
    let syncingTerminalDetail = false;

    const syncTerminalDetail = async () => {
      if (!active || syncingTerminalDetail) {
        return;
      }
      syncingTerminalDetail = true;
      try {
        const nextDetail = await getEmulationDetail(sessionId);
        if (!active) {
          return;
        }
        setError(null);
        setDetail(nextDetail);
        if (
          !shouldTrackLiveStatus(
            nextDetail.status,
            nextDetail.post_processing_status ?? null,
          )
        ) {
          setLiveStatus(null);
        }
      } catch {
        if (active) {
          setError("Не удалось загрузить детали сессии.");
        }
      } finally {
        syncingTerminalDetail = false;
      }
    };

    const startPollingFallback = () => {
      const poll = async () => {
        try {
          const runtime = await getEmulationStatus(sessionId);
          if (!active) {
            return;
          }
          setError(null);
          setLiveStatus(runtime);

          if (!shouldTrackLiveStatus(runtime.status, runtime.post_processing_status ?? null)) {
            await syncTerminalDetail();
            return;
          }

          fallbackTimer = window.setTimeout(() => {
            void poll();
          }, 2500);
        } catch {
          if (active) {
            setError("Не удалось загрузить живой статус сессии.");
          }
        }
      };

      void poll();
    };

    const stopStream = streamEmulationStatus(sessionId, {
      onStatus: (status) => {
        if (!active) {
          return;
        }
        setError(null);
        setLiveStatus(status);
        if (!shouldTrackLiveStatus(status.status, status.post_processing_status ?? null)) {
          void syncTerminalDetail();
        }
      },
      onClose: () => {
        if (!active) {
          return;
        }
        streamClosedGracefully = true;
        void syncTerminalDetail();
      },
      onError: () => {
        if (!active || streamClosedGracefully) {
          return;
        }
        startPollingFallback();
      },
    });

    return () => {
      active = false;
      stopStream();
      if (fallbackTimer) {
        window.clearTimeout(fallbackTimer);
      }
    };
  }, [detail?.status, detail?.post_processing_status, sessionId]);

  const showLiveRuntime = useMemo(
    () =>
      liveStatus
        ? shouldTrackLiveStatus(
            liveStatus.status,
            liveStatus.post_processing_status ?? null,
          )
        : false,
    [liveStatus],
  );

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
      post_processing_status:
        liveStatus.post_processing_status ?? detail.post_processing_status,
      post_processing_progress:
        liveStatus.post_processing_progress ?? detail.post_processing_progress,
      elapsed_minutes: liveStatus.elapsed_minutes ?? detail.elapsed_minutes,
      bytes_downloaded: liveStatus.bytes_downloaded,
      requested_topics:
        liveStatus.requested_topics.length > 0
          ? liveStatus.requested_topics
          : detail.requested_topics,
      topics_searched:
        liveStatus.topics_searched.length > 0
          ? liveStatus.topics_searched
          : detail.topics_searched,
      videos_watched: liveStatus.videos_watched,
      watched_videos_count: liveStatus.watched_videos_count,
      watched_videos: liveStatus.watched_videos,
      watched_ads_count: liveStatus.watched_ads_count,
      watched_ads: liveStatus.watched_ads,
      watched_ads_analytics: liveStatus.watched_ads_analytics,
      total_duration_seconds: liveStatus.total_duration_seconds ?? detail.total_duration_seconds,
      mode: liveStatus.mode ?? detail.mode,
      fatigue: liveStatus.fatigue ?? detail.fatigue,
      error: liveStatus.error ?? detail.error,
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
        const mergedPrimaryCapture = mergeCapture(liveCapture, persistedCaptures[0] ?? null);
        const adCaptures =
          persistedCaptures.length > 0
            ? [
                mergedPrimaryCapture ?? persistedCaptures[0],
                ...persistedCaptures.slice(1),
              ]
            : mergedPrimaryCapture
              ? [mergedPrimaryCapture]
              : [];

        return {
          position,
          ad,
          captures: adCaptures,
          primaryCapture: adCaptures[0] ?? null,
        };
      });
  }, [session]);

  const sessionVideos = useMemo<SessionVideoItem[]>(() => {
    const persisted = [...(session?.watched_videos ?? [])];
    const current = liveStatus?.current_watch;
    if (!current) {
      return persisted;
    }

    const alreadyTracked = persisted.some(
      (video) =>
        video.url === current.url ||
        (video.title === current.title &&
          (video.search_keyword ?? null) === (current.search_keyword ?? null)),
    );
    if (alreadyTracked) {
      return persisted;
    }

    return [
      {
        position: 0,
        action: current.action,
        title: current.title,
        url: current.url,
        watched_seconds: current.watched_seconds,
        target_seconds: current.target_seconds ?? current.watched_seconds,
        watch_ratio:
          current.target_seconds && current.target_seconds > 0
            ? current.watched_seconds / current.target_seconds
            : null,
        completed: false,
        search_keyword: current.search_keyword ?? null,
        matched_topics: current.matched_topics,
        keywords: current.keywords,
        recorded_at: current.started_at,
        runtime: true,
      },
      ...persisted,
    ];
  }, [session?.watched_videos, liveStatus?.current_watch]);

  const filteredAdCards = useMemo(() => {
    let cards = adCards;

    if (adFilter !== "all") {
      cards = cards.filter((item) => {
        const result = resolveAnalysisResult(item.primaryCapture);
        if (adFilter === "relevant") return result === "relevant";
        if (adFilter === "not_relevant") return result === "not_relevant";
        return result === null && item.primaryCapture?.analysis_status !== "failed";
      });
    }

    if (adSort === "duration") {
      cards = [...cards].sort((a, b) => {
        const dA = a.ad?.ad_duration_seconds ?? a.primaryCapture?.ad_duration_seconds ?? 0;
        const dB = b.ad?.ad_duration_seconds ?? b.primaryCapture?.ad_duration_seconds ?? 0;
        return dB - dA;
      });
    }

    return cards;
  }, [adCards, adFilter, adSort]);

  const hasAds = adCards.length > 0;
  const visibleAdCards = showAllAds ? filteredAdCards : filteredAdCards.slice(0, 4);
  const postProcessingLabel = getPostProcessingLabel(
    session?.post_processing_status,
    session?.status,
    session?.watched_ads_count ?? 0,
    session?.post_processing_progress,
  );
  const adAnalysisSummary = useMemo(() => {
    return adCards.reduce(
      (summary, item) => {
        const result = resolveAnalysisResult(item.primaryCapture);
        if (result === "relevant") {
          summary.relevant += 1;
        } else if (result === "not_relevant") {
          summary.notRelevant += 1;
        } else if (item.primaryCapture?.analysis_status === "pending") {
          summary.pending += 1;
        }
        return summary;
      },
      { relevant: 0, notRelevant: 0, pending: 0 },
    );
  }, [adCards]);

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
      const actionLabel =
        action === "stop"
          ? "остановить"
          : action === "retry"
            ? "перезапустить"
            : "продолжить";
      setError(`Не удалось ${actionLabel} сессию.`);
    }
  }

  if (loading) {
    return <Loader label="Загрузка деталей сессии" />;
  }

  if (!session) {
    return (
      <EmptyState
        title="Сессия недоступна"
        description={error ?? "Детали не были получены."}
      />
    );
  }

  return (
    <div className="space-y-6">
      {error ? (
        <Card className="border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {error}
        </Card>
      ) : null}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link to="/sessions" className="inline-flex items-center gap-1.5 text-sm text-[var(--brand)] transition hover:text-[var(--brand-strong)]">
            <ArrowLeft size={14} />
            Сессии
          </Link>
          <h2 className="mt-2 text-lg font-semibold text-[var(--ink)]">
            Сессия {session.session_id.slice(0, 12)}
          </h2>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Badge tone={getStatusTone(session.status) as never}>{formatSessionStatus(session.status)}</Badge>
            <span className="text-xs text-[var(--muted)]">
              {formatDate(session.queued_at)}
            </span>
          </div>
          {postProcessingLabel ? (
            <div className="mt-2 text-xs text-[var(--muted)]">{postProcessingLabel}</div>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2">
          {session.status === "running" ? (
            <Button variant="danger" onClick={() => void handleAction("stop")}>
              Остановить
            </Button>
          ) : null}
          {session.status === "failed" ? (
            <Button variant="secondary" onClick={() => void handleAction("retry")}>
              Повторить
            </Button>
          ) : null}
          {session.status === "stopped" ? (
            <Button onClick={() => void handleAction("resume")}>Продолжить остаток</Button>
          ) : null}
        </div>
      </div>

      <div className="metric-grid">
        <Card>
          <div className="text-xs font-medium uppercase tracking-wider text-[var(--muted)]">Запрошено</div>
          <div className="mt-2 text-2xl font-semibold text-[var(--ink)]">{session.requested_duration_minutes}m</div>
          <div className="mt-1 text-xs text-[var(--muted)]">Фактически {formatMinutes(session.elapsed_minutes)}</div>
        </Card>
        <Card>
          <div className="text-xs font-medium uppercase tracking-wider text-[var(--muted)]">Видео</div>
          <div className="mt-2 text-2xl font-semibold text-[var(--ink)]">{formatNumber(session.videos_watched)}</div>
          <div className="mt-1 text-xs text-[var(--muted)]">
            {formatNumber(session.watched_videos_count)} записей просмотра
          </div>
        </Card>
        <Card>
          <div className="text-xs font-medium uppercase tracking-wider text-[var(--muted)]">Реклама</div>
          <div className="mt-2 text-2xl font-semibold text-[var(--ink)]">{formatNumber(session.watched_ads_count)}</div>
          <div className="mt-1 text-xs text-[var(--muted)]">
            {session.watched_ads_count === 0
              ? "Анализ рекламы не требуется"
              : adAnalysisSummary.relevant > 0 || adAnalysisSummary.notRelevant > 0
              ? `${adAnalysisSummary.relevant} релевантно · ${adAnalysisSummary.notRelevant} не релевантно`
              : `${session.captures.video_captures} видеозаписей`}
          </div>
        </Card>
        <Card>
          <div className="text-xs font-medium uppercase tracking-wider text-[var(--muted)]">Трафик</div>
          <div className="mt-2 text-2xl font-semibold text-[var(--ink)]">{formatBytes(session.bytes_downloaded)}</div>
          <div className="mt-1 text-xs text-[var(--muted)]">скачано</div>
        </Card>
      </div>

      {showLiveRuntime && liveStatus ? (
        <div className="grid gap-4 xl:grid-cols-2">
          <Card className="p-5" glow>
            <div className="mb-3 flex items-center gap-2">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--accent)] opacity-75" />
                <span className="inline-flex h-2 w-2 rounded-full bg-[var(--accent)]" />
              </span>
              <span className="text-sm font-semibold text-[var(--ink)]">Живой рантайм</span>
            </div>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between gap-4">
                <span className="text-[var(--muted)]">Профиль</span>
                <span className="text-[var(--ink-secondary)]">{liveStatus.profile_id || "—"}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-[var(--muted)]">Прошло</span>
                <span className="text-[var(--ink-secondary)]">{formatMinutes(liveStatus.elapsed_minutes)}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-[var(--muted)]">Искали темы</span>
                <span className="text-[var(--ink-secondary)]">{liveStatus.topics_searched.join(", ") || "—"}</span>
              </div>
            </div>
          </Card>
          <Card className="p-5">
            <div className="mb-3 text-sm font-semibold text-[var(--ink)]">Текущий просмотр</div>
            {liveStatus.current_watch ? (
              <div className="space-y-2">
                <div className="text-base font-semibold text-[var(--ink)]">
                  {liveStatus.current_watch.title}
                </div>
                <div className="text-sm text-[var(--muted)]">
                  {liveStatus.current_watch.search_keyword || "без ключевого запроса"}
                </div>
                <div className="text-sm text-[var(--muted)]">
                  {formatWatchTiming(
                    liveStatus.current_watch.watched_seconds,
                    liveStatus.current_watch.target_seconds,
                  )}
                </div>
              </div>
            ) : (
              <div className="text-sm text-[var(--muted)]">
                Сессия активна, но текущее видео еще не отдано в статус.
              </div>
            )}
          </Card>
        </div>
      ) : null}

      <div className="inline-flex rounded-xl border border-[var(--line)] bg-[var(--panel-soft)] p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.6)]">
        {(["overview", ...(hasAds ? (["ads"] as const) : [])] as const).map((tab) => (
          <button
            key={tab}
            className={`inline-flex min-w-[122px] items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium capitalize transition-all ${
              activeTab === tab
                ? "bg-[var(--panel)] text-[var(--brand)] shadow-[0_1px_2px_rgba(0,0,0,0.06)]"
                : "text-[var(--muted)] hover:bg-[var(--panel)] hover:text-[var(--ink)]"
            }`}
            onClick={() => setActiveTab(tab)}
          >
            {tab === "overview" ? <LayoutPanelTop size={15} /> : <Megaphone size={15} />}
            <span>{tab === "overview" ? "Обзор" : "Реклама"}</span>
            {tab === "ads" ? (
              <span className="rounded-md bg-[var(--bg-soft)] px-1.5 py-0.5 text-[11px] font-semibold text-[var(--ink-secondary)]">
                {adCards.length}
              </span>
            ) : null}
          </button>
        ))}
      </div>

      {activeTab === "overview" ? (
        <div className="grid gap-4 xl:grid-cols-2">
          <Card className="p-5">
            <div className="mb-3 flex items-baseline justify-between gap-3">
              <div className="text-sm font-semibold text-[var(--ink)]">
                Темы
              </div>
              <div className="text-xs text-[var(--muted)]">
                {session.topics_searched.length}/{session.requested_topics.length} покрыто
              </div>
            </div>
            {session.requested_topics.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {session.requested_topics.map((topic) => {
                  const covered = session.topics_searched.some(
                    (s) => s.toLowerCase() === topic.toLowerCase(),
                  );
                  return (
                    <span
                      key={topic}
                      className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs ${
                        covered
                          ? "border-[var(--accent)]/20 bg-[var(--accent-soft)] text-[var(--accent)]"
                          : "border-[var(--line)] bg-[var(--panel)] text-[var(--muted)]"
                      }`}
                    >
                      {covered ? <Check size={11} strokeWidth={3} /> : <Clock size={11} />}
                      {topic}
                    </span>
                  );
                })}
              </div>
            ) : (
              <span className="text-sm text-[var(--muted)]">—</span>
            )}
          </Card>
          <Card className="p-5">
            <div className="mb-3 text-sm font-semibold text-[var(--ink)]">Метаданные сессии</div>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between gap-4">
                <span className="text-[var(--muted)]">Поставлена в очередь</span>
                <span className="text-[var(--ink-secondary)]">{formatDate(session.queued_at)}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-[var(--muted)]">Запущена</span>
                <span className="text-[var(--ink-secondary)]">{formatDate(session.started_at)}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-[var(--muted)]">Завершена</span>
                <span className="text-[var(--ink-secondary)]">{formatDate(session.finished_at)}</span>
              </div>
            </div>
          </Card>
          <Card className="p-0 xl:col-span-2">
            <div className="border-b border-[var(--line)] px-5 py-4 text-sm font-semibold text-[var(--ink)]">
              Просмотренные видео
            </div>
            {sessionVideos.length > 0 ? (
              <div className="divide-y divide-[var(--line)]">
                {sessionVideos.map((video) => (
                  <div key={`${video.position}-${video.recorded_at}`} className="px-5 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <div className="text-sm font-medium text-[var(--ink)]">{video.title}</div>
                        <div className="mt-0.5 text-xs text-[var(--muted)]">
                          {video.search_keyword || "без поискового запроса"} ·{" "}
                          {formatWatchTiming(video.watched_seconds, video.target_seconds)}
                        </div>
                      </div>
                      <Badge tone={video.runtime ? "info" : video.completed ? "success" : "warning"}>
                        {video.runtime ? "в процессе" : video.completed ? "завершено" : "частично"}
                      </Badge>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="px-5 py-8 text-sm text-[var(--muted)]">
                Для этой сессии пока нет записанных просмотров видео.
              </div>
            )}
          </Card>
        </div>
      ) : null}

      {activeTab === "ads" && hasAds ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="inline-flex rounded-xl border border-[var(--line)] bg-[var(--panel-soft)] p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.6)]">
              {(
                [
                  { key: "all", label: "Все", count: adCards.length },
                  { key: "relevant", label: "Релевантные", count: adAnalysisSummary.relevant },
                  { key: "not_relevant", label: "Не релевантные", count: adAnalysisSummary.notRelevant },
                  ...(adAnalysisSummary.pending > 0
                    ? [{ key: "pending", label: "Ожидают", count: adAnalysisSummary.pending }]
                    : []),
                ] as const
              ).map((item) => (
                <button
                  key={item.key}
                  className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-all ${
                    adFilter === item.key
                      ? "bg-[var(--panel)] text-[var(--brand)] shadow-[0_1px_2px_rgba(0,0,0,0.06)]"
                      : "text-[var(--muted)] hover:bg-[var(--panel)] hover:text-[var(--ink)]"
                  }`}
                  onClick={() => {
                    setAdFilter(item.key as typeof adFilter);
                    setShowAllAds(false);
                  }}
                >
                  {item.label}
                  <span className="rounded-md bg-[var(--bg-soft)] px-1.5 py-0.5 text-[10px] font-semibold text-[var(--ink-secondary)]">
                    {item.count}
                  </span>
                </button>
              ))}
            </div>

            <div className="inline-flex rounded-xl border border-[var(--line)] bg-[var(--panel-soft)] p-1">
              {(
                [
                  { key: "position", label: "Порядок" },
                  { key: "duration", label: "Длительность" },
                ] as const
              ).map((item) => (
                <button
                  key={item.key}
                  className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-all ${
                    adSort === item.key
                      ? "bg-[var(--panel)] text-[var(--brand)] shadow-[0_1px_2px_rgba(0,0,0,0.06)]"
                      : "text-[var(--muted)] hover:bg-[var(--panel)] hover:text-[var(--ink)]"
                  }`}
                  onClick={() => setAdSort(item.key)}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>

          {filteredAdCards.length === 0 ? (
            <Card className="px-5 py-8 text-center text-sm text-[var(--muted)]">
              По выбранному фильтру рекламы не найдено.
            </Card>
          ) : null}
          {visibleAdCards.map((item) => {
            const analysisResult = resolveAnalysisResult(item.primaryCapture);
            const analysisReason = readAnalysisField(item.primaryCapture, "reason");
            const analysisCategory = readAnalysisField(item.primaryCapture, "category");
            const adAnalysisLabel = getAnalysisLabel(
              analysisResult,
              item.primaryCapture?.analysis_status,
            );
            const mediaHiddenByAnalysis =
              analysisResult === "not_relevant" ||
              item.primaryCapture?.analysis_status === "not_relevant";
            const isExpanded = expandedAd === item.position;

            const accentColor = analysisResult === "relevant"
              ? "var(--accent)"
              : analysisResult === "not_relevant"
                ? "var(--warning)"
                : "transparent";

            const duration = item.ad?.ad_duration_seconds ?? item.primaryCapture?.ad_duration_seconds;
            const showDuration = typeof duration === "number" && duration > 0;
            const ctaText = item.ad?.cta_text;
            const showCta = ctaText &&
              ctaText.toLowerCase() !== "visit site" &&
              ctaText.toLowerCase() !== "перейти на сайт";

            const title = getAdCardTitle(item.ad, item.primaryCapture);
            const domain = getAdLandingHost(item.ad, item.primaryCapture) || "неизвестный домен";

            return (
              <Card key={`ad-card-${item.position}`} className="overflow-hidden p-0">
                <div style={{ borderLeft: `4px solid ${accentColor}` }}>
                  {/* Header — entire row clickable */}
                  <button
                    type="button"
                    onClick={() => setExpandedAd((prev) => (prev === item.position ? null : item.position))}
                    aria-expanded={isExpanded}
                    className="group flex w-full items-start gap-4 px-5 py-4 text-left transition hover:bg-[var(--panel-soft)]"
                  >
                    <div className="min-w-0 flex-1">
                      <h3 className="line-clamp-2 text-[15px] font-semibold leading-snug text-[var(--ink)]" title={title}>
                        {title}
                      </h3>
                      <div className="mt-2 flex flex-wrap items-center gap-1.5">
                        <span className="inline-flex items-center gap-1.5 rounded-md bg-[var(--bg-soft)] px-2 py-0.5 text-[11px] text-[var(--ink-secondary)]">
                          <Globe size={11} className="text-[var(--muted)]" />
                          <span className="font-mono">{domain}</span>
                        </span>
                        {showDuration ? (
                          <span className="inline-flex items-center gap-1 rounded-md bg-[var(--bg-soft)] px-2 py-0.5 text-[11px] text-[var(--ink-secondary)]">
                            <Clock size={11} className="text-[var(--muted)]" />
                            <span className="font-mono tabular-nums">{Number(duration).toFixed(1)}с</span>
                          </span>
                        ) : null}
                        {showCta ? (
                          <span className="inline-flex items-center gap-1 rounded-md bg-[var(--bg-soft)] px-2 py-0.5 text-[11px] text-[var(--ink-secondary)]">
                            <MousePointerClick size={11} className="text-[var(--muted)]" />
                            {ctaText}
                          </span>
                        ) : null}
                        {item.captures.length > 1 ? (
                          <span className="inline-flex items-center gap-1 rounded-md bg-[var(--bg-soft)] px-2 py-0.5 text-[11px] text-[var(--ink-secondary)]">
                            <span className="font-mono tabular-nums">{item.captures.length}</span> сегментов
                          </span>
                        ) : null}
                      </div>
                    </div>

                    <div className="flex shrink-0 items-center gap-2 pt-0.5">
                      {item.ad ? (
                        <Badge tone={item.ad.completed ? "success" : "warning"}>
                          {item.ad.completed ? "завершено" : "частично"}
                        </Badge>
                      ) : null}
                      {analysisResult ? (
                        <Badge tone={getAnalysisTone(analysisResult) as never}>
                          {adAnalysisLabel}
                        </Badge>
                      ) : item.primaryCapture?.analysis_status ? (
                        <Badge tone={getAnalysisTone(null) as never}>{adAnalysisLabel}</Badge>
                      ) : null}
                      <span className="ml-1 grid h-7 w-7 place-items-center rounded-full text-[var(--muted)] transition group-hover:bg-[var(--bg-soft)] group-hover:text-[var(--ink-secondary)]">
                        <ChevronDown
                          size={16}
                          className="transition-transform"
                          style={{ transform: isExpanded ? "rotate(180deg)" : "rotate(0deg)" }}
                        />
                      </span>
                    </div>
                  </button>

                  {/* Expanded body */}
                  {isExpanded ? (
                    <div className="space-y-5 border-t border-[var(--line)] bg-[var(--bg-soft)]/55 px-5 py-5">
                      {/* Capture status chips */}
                      {item.primaryCapture ? (
                        <div className="flex flex-wrap items-center gap-1.5">
                          <Badge tone={item.primaryCapture.video_status === "completed" ? "success" : "neutral"}>
                            видео: {formatCaptureStatus(item.primaryCapture.video_status)}
                          </Badge>
                          <span className="text-[11px] text-[var(--muted)]">·</span>
                          <Badge tone={item.primaryCapture.landing_status === "completed" ? "info" : "neutral"}>
                            лендинг: {formatCaptureStatus(item.primaryCapture.landing_status)}
                          </Badge>
                        </div>
                      ) : null}

                      {/* Ad full text */}
                      {item.ad?.full_text ? (
                        <div>
                          <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--muted)]">
                            текст рекламы
                          </div>
                          <blockquote className="border-l-2 border-[var(--line-strong)] pl-4 text-[14px] leading-relaxed text-[var(--ink-secondary)]">
                            <span className="whitespace-pre-wrap">{item.ad.full_text}</span>
                          </blockquote>
                        </div>
                      ) : null}

                      {/* Analysis */}
                      {item.primaryCapture?.analysis_status === "completed" || item.primaryCapture?.analysis_status === "not_relevant" ? (
                        <div>
                          <div className="mb-2 flex flex-wrap items-center gap-2">
                            <span className="text-[11px] font-semibold uppercase tracking-wider text-[var(--muted)]">анализ</span>
                            <Badge tone={getAnalysisTone(analysisResult) as never}>
                              <span className="inline-flex items-center gap-1">
                                <Sparkles size={10} /> {adAnalysisLabel}
                              </span>
                            </Badge>
                            {analysisCategory ? <Badge tone="info">{analysisCategory}</Badge> : null}
                          </div>
                          {analysisReason ? (
                            <p className="text-[14px] leading-relaxed text-[var(--ink-secondary)]">{analysisReason}</p>
                          ) : null}
                        </div>
                      ) : item.primaryCapture?.analysis_status === "pending" ? (
                        <div className="flex items-center gap-2 rounded-xl border border-dashed border-[var(--line-strong)] bg-[var(--panel-soft)] px-4 py-3 text-[13px] text-[var(--muted)]">
                          <span className="relative flex h-2 w-2">
                            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--brand)] opacity-50" />
                            <span className="relative inline-flex h-2 w-2 rounded-full bg-[var(--brand)]" />
                          </span>
                          анализ в процессе…
                        </div>
                      ) : item.primaryCapture?.analysis_status === "failed" ? (
                        <div className="text-[13px] text-[var(--muted)]">Рекламу не удалось проанализировать.</div>
                      ) : null}

                      {/* Media */}
                      {item.captures.length > 0 && !mediaHiddenByAnalysis ? (
                        <div>
                          <div className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-[var(--muted)]">
                            медиа{item.captures.length > 1 ? ` · ${item.captures.length} сегментов` : ""}
                          </div>
                          <div className="space-y-6">
                            {item.captures.map((capture, captureIndex) => (
                              <CaptureMediaPreview
                                key={`${capture.ad_position}-${captureIndex}`}
                                capture={capture}
                                index={captureIndex}
                                totalSegments={item.captures.length}
                              />
                            ))}
                          </div>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </Card>
            );
          })}
          {filteredAdCards.length > 4 ? (
            <div className="flex justify-center">
              <Button
                type="button"
                variant="ghost"
                onClick={() => setShowAllAds((prev) => !prev)}
              >
                {showAllAds ? "Показать меньше реклам" : `Показать все рекламы (${filteredAdCards.length})`}
              </Button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
