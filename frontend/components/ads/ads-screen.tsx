import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ExternalLink,
  Film,
  Globe,
  Megaphone,
  PlayCircle,
  RefreshCw,
  Search,
  X,
  CheckCircle,
  XCircle,
  Clock,
  Image,
  ChevronRight,
  BarChart3,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Loader } from "@/components/ui/loader";
import { apiClient } from "@/lib/api-client";
import { getEmulationHistory } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { EmulationAdCapture, EmulationHistoryItem } from "@/types/api";

// ─── Types ───────────────────────────────────────────────────────────────────

type AdEntry = EmulationAdCapture & {
  session_id: string;
  session_started_at: string | null;
  session_topics: string[];
  _index: number;
};

type AnalysisFilter = "all" | "relevant" | "not_relevant" | "pending";
type SortKey = "date" | "duration" | "domain";

// ─── Helpers ─────────────────────────────────────────────────────────────────

const _REDIRECT_HOSTS = new Set([
  "googleadservices.com", "www.googleadservices.com",
  "google.com", "www.google.com",
  "doubleclick.net", "www.doubleclick.net", "googleads.g.doubleclick.net",
  "consent.youtube.com",
]);

function extractCleanDomain(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url.includes("://") ? url : `https://${url}`);
    let host = u.hostname.replace(/^www\./, "");
    if (_REDIRECT_HOSTS.has(u.hostname)) return null;
    // play.google.com → extract app id
    if (host === "play.google.com") {
      const id = u.searchParams.get("id");
      return id ? id.split(".").slice(-2).join(".") : null;
    }
    return host || null;
  } catch {
    return null;
  }
}

function resolveAdIdentity(ad: AdEntry): { name: string; domain: string | null } {
  const summaryName = ad.analysis_summary?.["advertiser"] as string | undefined;
  const domain =
    extractCleanDomain(ad.advertiser_domain) ??
    extractCleanDomain(ad.display_url) ??
    extractCleanDomain(ad.landing_url) ??
    null;

  // Reject YouTube-channel headlines like "Subscribe to X."
  const headline = ad.headline_text ?? "";
  const isChannelHeadline = /^(subscribe to|подпишитесь на)/i.test(headline.trim());

  const name = summaryName ?? domain ?? (isChannelHeadline ? "" : headline) ?? "Неизвестный рекламодатель";
  return { name: name || "Неизвестный рекламодатель", domain };
}

function buildMediaPath(value: string | null | undefined) {
  if (!value) return null;
  const normalized = value.replace(/\\/g, "/").replace(/^\.\//, "");
  const isAbsolute = normalized.startsWith("/");
  const encoded = normalized
    .split("/")
    .filter(Boolean)
    .map((s) => encodeURIComponent(s))
    .join("/");
  return `/emulation/media/${isAbsolute ? "/" : ""}${encoded}`;
}

function useProtectedBlobUrl(value: string | null | undefined) {
  const mediaPath = useMemo(() => buildMediaPath(value), [value]);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!mediaPath) { setBlobUrl(null); return; }
    let active = true;
    let objectUrl: string | null = null;
    void apiClient.get<Blob>(mediaPath, { responseType: "blob" }).then((r) => {
      if (!active) return;
      objectUrl = URL.createObjectURL(r.data);
      setBlobUrl(objectUrl);
    }).catch(() => { if (active) setBlobUrl(null); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [mediaPath]);

  return blobUrl;
}

function getAnalysisResult(capture: EmulationAdCapture): "relevant" | "not_relevant" | null {
  const r = capture.analysis_summary?.["result"];
  if (r === "relevant" || r === "not_relevant") return r;
  if (capture.analysis_status === "completed") return "relevant";
  if (capture.analysis_status === "not_relevant") return "not_relevant";
  return null;
}

// ─── Thumbnail ───────────────────────────────────────────────────────────────

function AdThumbnail({ ad }: { ad: AdEntry }) {
  const firstShot = ad.screenshot_paths[0];
  const shotUrl = firstShot ? buildMediaPath(firstShot.file_path) : null;
  const hasVideo = Boolean(ad.video_file && ad.video_status === "completed");

  if (shotUrl) {
    return (
      <div className="relative w-full overflow-hidden rounded-xl bg-[var(--panel-soft)]" style={{ aspectRatio: "16/9" }}>
        <img src={shotUrl} alt="" className="h-full w-full object-cover" />
        {hasVideo && (
          <div className="absolute bottom-2 right-2 flex items-center gap-1 rounded-lg bg-black/70 px-2 py-1 text-[10px] font-semibold text-white backdrop-blur-sm">
            <Film size={10} /> Видео
          </div>
        )}
      </div>
    );
  }

  if (hasVideo) {
    return (
      <div className="flex w-full items-center justify-center rounded-xl bg-[var(--panel-soft)]" style={{ aspectRatio: "16/9" }}>
        <Film size={28} className="text-[var(--muted)]" />
      </div>
    );
  }

  return (
    <div className="flex w-full items-center justify-center rounded-xl bg-[var(--panel-soft)]" style={{ aspectRatio: "16/9" }}>
      <Image size={28} className="text-[var(--muted)]" />
    </div>
  );
}

// ─── Result pill ─────────────────────────────────────────────────────────────

function ResultPill({ ad }: { ad: AdEntry }) {
  const result = getAnalysisResult(ad);
  const isPending = !result && (ad.analysis_status === "pending" || ad.analysis_status === "queued");

  if (result === "relevant") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">
        <CheckCircle size={10} /> Релевантно
      </span>
    );
  }
  if (result === "not_relevant") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700">
        <XCircle size={10} /> Не релевантно
      </span>
    );
  }
  if (isPending) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2 py-0.5 text-[11px] font-semibold text-blue-600">
        <Clock size={10} /> Анализ…
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-[var(--panel-soft)] px-2 py-0.5 text-[11px] font-medium text-[var(--muted)]">
      Нет анализа
    </span>
  );
}

// ─── Ad Card ─────────────────────────────────────────────────────────────────

function AdCard({ ad, onClick }: { ad: AdEntry; onClick: () => void }) {
  const { name: advertiser, domain } = resolveAdIdentity(ad);
  const category = ad.analysis_summary?.["category"] as string | undefined;

  return (
    <button
      onClick={onClick}
      className="group flex flex-col overflow-hidden rounded-2xl border border-[var(--line)] bg-white text-left transition-all hover:border-[var(--brand)] hover:shadow-lg"
    >
      {/* Thumbnail */}
      <div className="p-3 pb-0">
        <AdThumbnail ad={ad} />
      </div>

      {/* Info */}
      <div className="flex flex-1 flex-col gap-2 p-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-[var(--ink)]">{advertiser}</div>
            {domain && domain !== advertiser && (
              <div className="truncate text-xs text-[var(--muted)]">{domain}</div>
            )}
          </div>
          <ChevronRight size={14} className="mt-0.5 shrink-0 text-[var(--muted)] transition-transform group-hover:translate-x-0.5" />
        </div>

        {ad.headline_text && (
          <p className="line-clamp-2 text-xs text-[var(--ink-secondary)]">{ad.headline_text}</p>
        )}

        <div className="mt-auto flex items-center justify-between gap-2">
          <ResultPill ad={ad} />
          <div className="flex items-center gap-2">
            {ad.ad_duration_seconds != null && (
              <span className="text-[11px] text-[var(--muted)]">{ad.ad_duration_seconds.toFixed(0)}с</span>
            )}
            {category && (
              <span className="rounded-full bg-[var(--panel-soft)] px-2 py-0.5 text-[11px] text-[var(--muted)]">{category}</span>
            )}
          </div>
        </div>

        <div className="text-[11px] text-[var(--muted)]">{formatDate(ad.session_started_at ?? "")}</div>
      </div>
    </button>
  );
}

// ─── Modal ───────────────────────────────────────────────────────────────────

function VideoPlayerModal({ videoFile }: { videoFile: string | null | undefined }) {
  const blobUrl = useProtectedBlobUrl(videoFile);
  if (!videoFile) return null;
  return blobUrl ? (
    <video src={blobUrl} controls className="w-full rounded-xl" style={{ maxHeight: 360 }} />
  ) : (
    <div className="flex h-40 items-center justify-center rounded-xl bg-[var(--panel-soft)] text-sm text-[var(--muted)]">
      <Film size={20} className="mr-2" /> Загрузка…
    </div>
  );
}

function AdModal({ ad, onClose }: { ad: AdEntry; onClose: () => void }) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const result = getAnalysisResult(ad);
  const { name: advertiser, domain } = resolveAdIdentity(ad);
  const reason = ad.analysis_summary?.["reason"] as string | undefined;
  const category = ad.analysis_summary?.["category"] as string | undefined;
  const hasVideo = Boolean(ad.video_file && ad.video_status === "completed");

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
      onClick={(e) => { if (e.target === overlayRef.current) onClose(); }}
    >
      <div className="relative flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[var(--line)] px-5 py-4">
          <div className="min-w-0">
            <div className="truncate font-semibold text-[var(--ink)]">{advertiser}</div>
            {domain && domain !== advertiser && (
              <div className="truncate text-xs text-[var(--muted)]">{domain}</div>
            )}
          </div>
          <button
            onClick={onClose}
            className="ml-3 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl text-[var(--muted)] hover:bg-[var(--panel-soft)] hover:text-[var(--ink)] transition"
          >
            <X size={16} />
          </button>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto">
          <div className="space-y-4 p-5">
            {/* Analysis banner */}
            {result && (
              <div className={`rounded-xl px-4 py-3 ${result === "relevant" ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-800"}`}>
                <div className="flex items-center gap-2 font-semibold text-sm">
                  {result === "relevant"
                    ? <><CheckCircle size={15} /> Релевантная реклама</>
                    : <><XCircle size={15} /> Не релевантная реклама</>}
                  {category && <span className="font-normal opacity-60">· {category}</span>}
                </div>
                {reason && <p className="mt-1 text-xs opacity-75">{reason}</p>}
              </div>
            )}

            {/* Video */}
            {hasVideo && <VideoPlayerModal videoFile={ad.video_file} />}

            {/* Screenshots */}
            {ad.screenshot_paths.length > 0 && (
              <div>
                <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">Скриншоты</div>
                <div className="flex gap-2 overflow-x-auto pb-1">
                  {ad.screenshot_paths.map((p) => {
                    const url = buildMediaPath(p.file_path);
                    return url ? (
                      <a key={p.offset_ms} href={url} target="_blank" rel="noreferrer" className="shrink-0">
                        <img
                          src={url}
                          alt=""
                          className="h-24 w-40 rounded-lg object-cover border border-[var(--line)] hover:opacity-80 transition"
                        />
                      </a>
                    ) : null;
                  })}
                </div>
              </div>
            )}

            {/* Headline */}
            {ad.headline_text && (
              <div>
                <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">Заголовок</div>
                <p className="text-sm text-[var(--ink)]">{ad.headline_text}</p>
              </div>
            )}

            {/* Meta */}
            <div className="grid grid-cols-2 gap-3">
              {ad.ad_duration_seconds != null && (
                <div className="rounded-xl bg-[var(--panel-soft)] px-4 py-3">
                  <div className="text-[11px] text-[var(--muted)]">Длительность</div>
                  <div className="font-semibold text-[var(--ink)]">{ad.ad_duration_seconds.toFixed(0)}с</div>
                </div>
              )}
              <div className="rounded-xl bg-[var(--panel-soft)] px-4 py-3">
                <div className="text-[11px] text-[var(--muted)]">Дата</div>
                <div className="font-semibold text-[var(--ink)]">{formatDate(ad.session_started_at ?? "")}</div>
              </div>
            </div>

            {/* Topics */}
            {ad.session_topics.length > 0 && (
              <div>
                <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">Темы сессии</div>
                <div className="flex flex-wrap gap-1.5">
                  {ad.session_topics.map((t) => (
                    <span key={t} className="rounded-lg bg-[var(--panel-soft)] px-2.5 py-1 text-xs text-[var(--ink-secondary)]">{t}</span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center gap-2 border-t border-[var(--line)] px-5 py-3">
          {ad.landing_url && (
            <a
              href={ad.landing_url}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1.5 rounded-lg border border-[var(--line)] px-3 py-1.5 text-xs text-[var(--ink-secondary)] hover:bg-[var(--panel-soft)] transition"
            >
              <ExternalLink size={12} /> Лендинг
            </a>
          )}
          {ad.cta_href && (
            <a
              href={ad.cta_href}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1.5 rounded-lg border border-[var(--line)] px-3 py-1.5 text-xs text-[var(--ink-secondary)] hover:bg-[var(--panel-soft)] transition"
            >
              <ExternalLink size={12} /> CTA
            </a>
          )}
          <Link
            to={`/sessions/${ad.session_id}`}
            className="ml-auto flex items-center gap-1.5 rounded-lg bg-[var(--brand)] px-3 py-1.5 text-xs font-semibold text-white hover:opacity-90 transition"
            onClick={onClose}
          >
            <PlayCircle size={12} /> Открыть сессию
          </Link>
        </div>
      </div>
    </div>
  );
}

// ─── Filter pill button ───────────────────────────────────────────────────────

function FilterPill({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-full border px-3.5 py-1.5 text-xs font-medium transition ${
        active
          ? "border-[var(--brand)] bg-[var(--brand)] text-white"
          : "border-[var(--line)] bg-white text-[var(--muted)] hover:border-[var(--brand)] hover:text-[var(--brand)]"
      }`}
    >
      {children}
    </button>
  );
}

// ─── Main screen ─────────────────────────────────────────────────────────────

export function AdsScreen() {
  const [items, setItems] = useState<EmulationHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedAd, setSelectedAd] = useState<AdEntry | null>(null);

  const [search, setSearch] = useState("");
  const [analysisFilter, setAnalysisFilter] = useState<AnalysisFilter>("all");
  const [sortBy, setSortBy] = useState<SortKey>("date");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getEmulationHistory({ has_ads: true, include_captures: true, page_size: 100, page: 1 });
      setItems(data.items);
    } catch {
      setError("Не удалось загрузить рекламы");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const allAds: AdEntry[] = useMemo(() => {
    const result: AdEntry[] = [];
    for (const item of items) {
      for (let i = 0; i < (item.ad_captures ?? []).length; i++) {
        result.push({
          ...item.ad_captures![i],
          session_id: item.session_id,
          session_started_at: item.started_at ?? null,
          session_topics: item.requested_topics,
          _index: result.length,
        });
      }
    }
    return result;
  }, [items]);

  const stats = useMemo(() => {
    const total = allAds.length;
    const relevant = allAds.filter((a) => getAnalysisResult(a) === "relevant").length;
    const notRelevant = allAds.filter((a) => getAnalysisResult(a) === "not_relevant").length;
    const withVideo = allAds.filter((a) => a.video_file && a.video_status === "completed").length;
    const relevantPct = total > 0 ? Math.round((relevant / total) * 100) : 0;
    return { total, relevant, notRelevant, withVideo, relevantPct };
  }, [allAds]);

  const topDomains = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const ad of allAds) {
      const { domain } = resolveAdIdentity(ad);
      const key = domain ?? "unknown";
      if (key === "unknown") continue;
      counts[key] = (counts[key] ?? 0) + 1;
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 6);
  }, [allAds]);

  const filtered = useMemo(() => {
    let result = allAds;
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter((a) => {
        const { name, domain } = resolveAdIdentity(a);
        return (
          name.toLowerCase().includes(q) ||
          (domain ?? "").toLowerCase().includes(q) ||
          (a.headline_text ?? "").toLowerCase().includes(q) ||
          a.session_topics.some((t) => t.toLowerCase().includes(q))
        );
      });
    }
    if (analysisFilter !== "all") {
      result = result.filter((a) => {
        const r = getAnalysisResult(a);
        if (analysisFilter === "pending") return r === null;
        return r === analysisFilter;
      });
    }
    return [...result].sort((a, b) => {
      if (sortBy === "duration") return (b.ad_duration_seconds ?? 0) - (a.ad_duration_seconds ?? 0);
      if (sortBy === "domain") return (a.advertiser_domain ?? "").localeCompare(b.advertiser_domain ?? "");
      return (b.session_started_at ?? "").localeCompare(a.session_started_at ?? "");
    });
  }, [allAds, search, analysisFilter, sortBy]);

  if (loading) return <Loader label="Загрузка реклам…" />;
  if (error) return (
    <div className="flex h-64 flex-col items-center justify-center gap-4">
      <p className="text-sm text-[var(--danger)]">{error}</p>
      <Button onClick={load}>Повторить</Button>
    </div>
  );

  return (
    <>
      {selectedAd && <AdModal ad={selectedAd} onClose={() => setSelectedAd(null)} />}

      <div className="mx-auto max-w-[1400px] px-4 py-6 md:px-6">
        {/* ── Header ── */}
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-[var(--ink)]">Реклама</h1>
            <p className="mt-0.5 text-sm text-[var(--muted)]">Все захваченные ролики по всем сессиям</p>
          </div>
          <button
            onClick={load}
            className="flex items-center gap-2 rounded-xl border border-[var(--line)] bg-white px-3 py-2 text-sm text-[var(--muted)] hover:bg-[var(--panel-soft)] transition"
          >
            <RefreshCw size={14} /> Обновить
          </button>
        </div>

        <div className="flex gap-6">
          {/* ── Sidebar ── */}
          <aside className="hidden w-60 shrink-0 lg:block">
            <div className="sticky top-6 space-y-4">
              {/* Stats */}
              <div className="rounded-2xl border border-[var(--line)] bg-white p-4 space-y-3">
                <div className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">Статистика</div>

                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-sm text-[var(--ink-secondary)]">
                    <Megaphone size={14} className="text-[var(--brand)]" /> Всего
                  </div>
                  <span className="font-bold text-[var(--ink)]">{stats.total}</span>
                </div>

                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-sm text-[var(--ink-secondary)]">
                    <CheckCircle size={14} className="text-emerald-600" /> Релевантных
                  </div>
                  <span className="font-bold text-emerald-700">{stats.relevant}</span>
                </div>

                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-sm text-[var(--ink-secondary)]">
                    <XCircle size={14} className="text-amber-500" /> Не релевантных
                  </div>
                  <span className="font-bold text-amber-700">{stats.notRelevant}</span>
                </div>

                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-sm text-[var(--ink-secondary)]">
                    <Film size={14} className="text-blue-500" /> С видео
                  </div>
                  <span className="font-bold text-[var(--ink)]">{stats.withVideo}</span>
                </div>

                {stats.total > 0 && (
                  <>
                    <div className="h-px bg-[var(--line)]" />
                    <div>
                      <div className="mb-1.5 flex items-center justify-between text-xs text-[var(--muted)]">
                        <span className="flex items-center gap-1"><BarChart3 size={11} /> Релевантность</span>
                        <span className="font-semibold text-[var(--ink)]">{stats.relevantPct}%</span>
                      </div>
                      <div className="h-2 overflow-hidden rounded-full bg-[var(--panel-soft)]">
                        <div
                          className="h-full rounded-full bg-emerald-500 transition-all"
                          style={{ width: `${stats.relevantPct}%` }}
                        />
                      </div>
                    </div>
                  </>
                )}
              </div>

              {/* Top advertisers */}
              {topDomains.length > 0 && (
                <div className="rounded-2xl border border-[var(--line)] bg-white p-4 space-y-2">
                  <div className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">Рекламодатели</div>
                  {topDomains.map(([domain, count]) => (
                    <button
                      key={domain}
                      onClick={() => setSearch(search === domain ? "" : domain)}
                      className={`flex w-full items-center justify-between rounded-lg px-2 py-1.5 text-xs transition ${
                        search === domain
                          ? "bg-[var(--brand-soft)] text-[var(--brand)]"
                          : "text-[var(--ink-secondary)] hover:bg-[var(--panel-soft)]"
                      }`}
                    >
                      <span className="flex items-center gap-1.5 min-w-0">
                        <Globe size={11} className="shrink-0" />
                        <span className="truncate">{domain}</span>
                      </span>
                      <span className={`ml-2 shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-bold ${
                        search === domain ? "bg-[var(--brand)] text-white" : "bg-[var(--panel-soft)] text-[var(--muted)]"
                      }`}>
                        {count}
                      </span>
                    </button>
                  ))}
                </div>
              )}

              {/* Sort */}
              <div className="rounded-2xl border border-[var(--line)] bg-white p-4 space-y-2">
                <div className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">Сортировка</div>
                {(["date", "duration", "domain"] as SortKey[]).map((s) => (
                  <button
                    key={s}
                    onClick={() => setSortBy(s)}
                    className={`flex w-full items-center rounded-lg px-2 py-1.5 text-xs transition ${
                      sortBy === s
                        ? "bg-[var(--brand-soft)] font-semibold text-[var(--brand)]"
                        : "text-[var(--ink-secondary)] hover:bg-[var(--panel-soft)]"
                    }`}
                  >
                    {s === "date" ? "По дате" : s === "duration" ? "По длительности" : "По домену"}
                  </button>
                ))}
              </div>
            </div>
          </aside>

          {/* ── Main content ── */}
          <div className="min-w-0 flex-1 space-y-4">
            {/* Search */}
            <div className="relative">
              <Search size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-[var(--muted)]" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Поиск по домену, заголовку, теме…"
                className="w-full rounded-xl border border-[var(--line)] bg-white py-2.5 pl-10 pr-9 text-sm outline-none focus:border-[var(--brand)] transition"
              />
              {search && (
                <button
                  onClick={() => setSearch("")}
                  className="absolute right-3.5 top-1/2 -translate-y-1/2 text-[var(--muted)] hover:text-[var(--ink)]"
                >
                  <X size={14} />
                </button>
              )}
            </div>

            {/* Quick filters */}
            <div className="flex items-center gap-2 overflow-x-auto pb-0.5">
              <FilterPill active={analysisFilter === "all"} onClick={() => setAnalysisFilter("all")}>
                Все <span className="ml-1 opacity-60">{allAds.length}</span>
              </FilterPill>
              <FilterPill active={analysisFilter === "relevant"} onClick={() => setAnalysisFilter("relevant")}>
                ✓ Релевантные <span className="ml-1 opacity-70">{stats.relevant}</span>
              </FilterPill>
              <FilterPill active={analysisFilter === "not_relevant"} onClick={() => setAnalysisFilter("not_relevant")}>
                ✗ Не релевантные <span className="ml-1 opacity-70">{stats.notRelevant}</span>
              </FilterPill>
              <FilterPill active={analysisFilter === "pending"} onClick={() => setAnalysisFilter("pending")}>
                Без анализа
              </FilterPill>
            </div>

            {/* Count */}
            {filtered.length !== allAds.length && (
              <p className="text-xs text-[var(--muted)]">
                Показано {filtered.length} из {allAds.length}
                <button onClick={() => { setSearch(""); setAnalysisFilter("all"); }} className="ml-2 text-[var(--brand)] hover:underline">
                  Сбросить
                </button>
              </p>
            )}

            {/* Grid */}
            {filtered.length === 0 ? (
              <EmptyState
                title="Рекламы не найдены"
                description={
                  allAds.length === 0
                    ? "Запустите эмуляцию — захваченные рекламы появятся здесь"
                    : "Нет реклам по текущим фильтрам"
                }
                action={
                  allAds.length > 0 ? (
                    <Button onClick={() => { setSearch(""); setAnalysisFilter("all"); }}>Сбросить фильтры</Button>
                  ) : undefined
                }
              />
            ) : (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {filtered.map((ad) => (
                  <AdCard
                    key={`${ad.session_id}-${ad.ad_position}-${ad._index}`}
                    ad={ad}
                    onClick={() => setSelectedAd(ad)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
