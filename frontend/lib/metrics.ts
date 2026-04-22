import type { EmulationAdCapture, EmulationHistoryItem } from "@/types/api";

function getAnalysisResult(capture: EmulationAdCapture) {
  const result = capture.analysis_summary?.result;
  return typeof result === "string" ? result : null;
}

export function getStatusTone(status: string) {
  switch (status) {
    case "completed":
      return "success";
    case "running":
      return "info";
    case "stopping":
      return "warning";
    case "failed":
      return "danger";
    case "stopped":
      return "warning";
    default:
      return "neutral";
  }
}

export function formatSessionStatus(status: string) {
  switch (status) {
    case "queued":
      return "В очереди";
    case "running":
      return "Запущена";
    case "stopping":
      return "Останавливается";
    case "completed":
      return "Завершена";
    case "failed":
      return "Ошибка";
    case "stopped":
      return "Остановлена";
    default:
      return status;
  }
}

export function aggregateCaptures(items: EmulationHistoryItem[]) {
  const captures = items.flatMap((item) => item.ad_captures ?? []);
  const landingCompleted = captures.filter((capture) => capture.landing_status === "completed").length;
  const videoCompleted = captures.filter((capture) => capture.video_status === "completed").length;
  const relevantAds = captures.filter(
    (capture) =>
      capture.analysis_status === "completed" || getAnalysisResult(capture) === "relevant",
  ).length;
  const rejectedAds = captures.filter(
    (capture) =>
      capture.analysis_status === "not_relevant" ||
      getAnalysisResult(capture) === "not_relevant",
  ).length;
  const analyzedAds = relevantAds + rejectedAds;

  return {
    totalAds: items.reduce((acc, item) => acc + item.captures.ads_total, 0),
    totalVideos: items.reduce((acc, item) => acc + item.videos_watched, 0),
    totalWatchedAds: items.reduce((acc, item) => acc + item.watched_ads_count, 0),
    totalSessions: items.length,
    videoCaptures: items.reduce((acc, item) => acc + item.captures.video_captures, 0),
    screenshotFallbacks: items.reduce((acc, item) => acc + item.captures.screenshot_fallbacks, 0),
    landingCompleted,
    landingFailed: captures.length - landingCompleted,
    captureItems: captures,
    running: items.filter((item) => item.status === "running").length,
    completed: items.filter((item) => item.status === "completed").length,
    failed: items.filter((item) => item.status === "failed").length,
    stopped: items.filter((item) => item.status === "stopped").length,
    videoCompleted,
    relevantAds,
    rejectedAds,
    analyzedAds,
  };
}

export function topAdvertisers(captures: EmulationAdCapture[]) {
  const counts = new Map<string, number>();
  for (const capture of captures) {
    const key = capture.advertiser_domain || "unknown";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6)
    .map(([label, value]) => ({ label, value }));
}

export function topTopics(items: EmulationHistoryItem[]) {
  const counts = new Map<string, number>();
  for (const item of items) {
    for (const topic of item.requested_topics) {
      counts.set(topic, (counts.get(topic) ?? 0) + 1);
    }
  }

  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([label, value]) => ({ label, value }));
}
