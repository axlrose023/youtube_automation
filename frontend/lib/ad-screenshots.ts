export type AdScreenshotPath = {
  offset_ms: number;
  file_path: string;
  kind?: string | null;
};

function normalizedKind(shot: AdScreenshotPath) {
  return (shot.kind ?? "").trim().toLowerCase();
}

function normalizedPath(shot: AdScreenshotPath) {
  return shot.file_path.replace(/\\/g, "/").toLowerCase();
}

export function isLandingScreenshot(shot: AdScreenshotPath) {
  const kind = normalizedKind(shot);
  if (kind === "landing" || kind === "landing_screenshot") {
    return true;
  }
  return /(^|[/_-])landing([._/-]|$)/.test(normalizedPath(shot));
}

export function isYoutubePreClickScreenshot(shot: AdScreenshotPath) {
  const kind = normalizedKind(shot);
  if (["youtube_pre_click", "pre_click", "youtube", "ad_pre_click"].includes(kind)) {
    return true;
  }
  const path = normalizedPath(shot);
  return (
    path.includes("youtube_pre_click") ||
    path.includes("cta_pre") ||
    path.includes("watch_banner") ||
    path.includes("banner_") ||
    path.includes("shorts_banner") ||
    path.includes("shorts_ad")
  ) && !isLandingScreenshot(shot);
}

export function getPreferredAdScreenshotIndex(paths: AdScreenshotPath[] | null | undefined) {
  if (!paths?.length) {
    return -1;
  }
  const explicit = paths.findIndex(isYoutubePreClickScreenshot);
  if (explicit >= 0) {
    return explicit;
  }
  const nonLanding = paths.findIndex((shot) => !isLandingScreenshot(shot));
  return nonLanding >= 0 ? nonLanding : 0;
}

export function getPreferredAdScreenshot(paths: AdScreenshotPath[] | null | undefined) {
  const index = getPreferredAdScreenshotIndex(paths);
  return index >= 0 && paths ? paths[index] : null;
}
