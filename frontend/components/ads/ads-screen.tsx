import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  BarChart2,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  FileImage,
  Film,
  Filter,
  Globe,
  Megaphone,
  PlayCircle,
  RefreshCw,
  Search,
  SlidersHorizontal,
  TrendingUp,
  X,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Loader } from "@/components/ui/loader";
import { apiClient } from "@/lib/api-client";
import { getEmulationHistory } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { EmulationAdCapture, EmulationHistoryItem } from "@/types/api";

type AdEntry = EmulationAdCapture & {
  session_id: string;
  session_started_at: string | null;
  session_topics: string[];
  _index: number;
};

type SortKey = "date" | "duration" | "domain";
type AnalysisFilter = "all" | "relevant" | "not_relevant" | "pending";
type MediaFilter = "all" | "video" | "screenshot";

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

function getAnalysisResult(capture: EmulationAdCapture): string | null {
  const r = capture.analysis_summary?.["result"];
  if (r === "relevant" || r === "not_relevant") return r as string;
  if (capture.analysis_status === "completed") return "relevant";
  if (capture.analysis_status === "not_relevant") return "not_relevant";
  return null;
}

function AnalysisBadge({ capture }: { capture: EmulationAdCapture }) {
  const result = getAnalysisResult(capture);
  if (result === "relevant") return <Badge tone="success">Релевантно</Badge>;
  if (result === "not_relevant") return <Badge tone="warning">Не релевантно</Badge>;
  if (capture.analysis_status === "failed") return <Badge tone="danger">Ошибка анализа</Badge>;
  if (capture.analysis_status === "pending" || capture.analysis_status === "queued")
    return <Badge tone="info">Анализируется</Badge>;
  return <Badge tone="neutral">Нет анализа</Badge>;
}

function VideoPlayer({ videoFile }: { videoFile: string | null | undefined }) {
  const blobUrl = useProtectedBlobUrl(videoFile);
  if (!videoFile) return null;
  return (
    <div className="overflow-hidden rounded-xl bg-black">
      {blobUrl ? (
        <video
          src={blobUrl}
          controls
          className="w-full"
          style={{ maxHeight: 280 }}
        />
      ) : (
        <div className="flex h-40 items-center justify-center text-white/40 text-sm">
          <Film size={20} className="mr-2" /> Загрузка видео…
        </div>
      )}
    </div>
  );
}

function ScreenshotStrip({ paths }: { paths: Array<{ offset_ms: number; file_path: string }> }) {
  if (!paths.length) return null;
  const shown = paths.slice(0, 4);
  return (
    <div className="flex gap-2 overflow-x-auto pb-1">
      {shown.map((p) => {
        const url = buildMediaPath(p.file_path);
        return url ? (
          <a key={p.offset_ms} href={url} target="_blank" rel="noreferrer" className="shrink-0">
            <img
              src={url}
              alt={`screenshot ${p.offset_ms}ms`}
              className="h-20 w-32 rounded-lg object-cover border border-[var(--line)] hover:opacity-80 transition"
            />
          </a>
        ) : null;
      })}
    </div>
  );
}

function AdCard({ ad }: { ad: AdEntry }) {
  const [expanded, setExpanded] = useState(false);
  const result = getAnalysisResult(ad);
  const hasVideo = Boolean(ad.video_file && ad.video_status === "completed");
  const hasScreenshots = ad.screenshot_paths.length > 0;
  const domain = ad.advertiser_domain ?? ad.display_url ?? "—";
  const reason = ad.analysis_summary?.["reason"] as string | undefined;
  const category = ad.analysis_summary?.["category"] as string | undefined;
  const advertiser = ad.analysis_summary?.["advertiser"] as string | undefined;

  return (
    <div
      className="rounded-2xl border border-[var(--line)] bg-white transition-shadow hover:shadow-md overflow-hidden"
    >
      {/* Header strip */}
      <div
        className="flex items-center justify-between gap-3 px-5 py-4 cursor-pointer select-none"
        onClick={() => setExpanded((e) => !e)}
      >
        <div className="flex items-center gap-3 min-w-0">
          {/* Domain avatar */}
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[var(--brand-soft)]">
            <Globe size={18} className="text-[var(--brand)]" />
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-[var(--ink)]">
              {advertiser ?? domain}
            </div>
            <div className="truncate text-xs text-[var(--muted)]">{domain}</div>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {hasVideo && (
            <span className="flex items-center gap-1 rounded-lg bg-[var(--info-soft)] px-2 py-1 text-xs font-medium text-[var(--info)]">
              <Film size={12} /> Видео
            </span>
          )}
          {hasScreenshots && (
            <span className="flex items-center gap-1 rounded-lg bg-[var(--panel-soft)] px-2 py-1 text-xs font-medium text-[var(--ink-secondary)]">
              <FileImage size={12} /> {ad.screenshot_paths.length}
            </span>
          )}
          <AnalysisBadge capture={ad} />
          {expanded ? <ChevronUp size={16} className="text-[var(--muted)]" /> : <ChevronDown size={16} className="text-[var(--muted)]" />}
        </div>
      </div>

      {/* Meta row */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-[var(--line)] bg-[var(--panel-soft)] px-5 py-2">
        {ad.headline_text && (
          <span className="truncate text-xs text-[var(--ink-secondary)] max-w-xs" title={ad.headline_text}>
            {ad.headline_text}
          </span>
        )}
        {ad.ad_duration_seconds != null && (
          <span className="text-xs text-[var(--muted)]">
            {ad.ad_duration_seconds.toFixed(0)}с ролик
          </span>
        )}
        <span className="text-xs text-[var(--muted)]">
          {formatDate(ad.session_started_at ?? "")}
        </span>
        <Link
          to={`/sessions/${ad.session_id}`}
          onClick={(e) => e.stopPropagation()}
          className="flex items-center gap-1 text-xs text-[var(--brand)] hover:underline"
        >
          <PlayCircle size={11} /> Сессия
        </Link>
      </div>

      {/* Expanded content */}
      {expanded && (
        <div className="border-t border-[var(--line)] px-5 py-4 space-y-4">
          {/* Analysis result */}
          {result && (
            <div className={`rounded-xl px-4 py-3 text-sm ${result === "relevant" ? "bg-[var(--accent-soft)] text-[var(--accent)]" : "bg-[var(--warning-soft)] text-[var(--warning)]"}`}>
              <div className="font-semibold mb-0.5">
                {result === "relevant" ? "Релевантная реклама" : "Не релевантная реклама"}
                {category && <span className="ml-2 font-normal opacity-70">· {category}</span>}
              </div>
              {reason && <div className="text-xs opacity-80">{reason}</div>}
            </div>
          )}

          {/* Video */}
          {hasVideo && <VideoPlayer videoFile={ad.video_file} />}

          {/* Screenshots */}
          {hasScreenshots && <ScreenshotStrip paths={ad.screenshot_paths} />}

          {/* Links */}
          <div className="flex flex-wrap gap-2">
            {ad.landing_url && (
              <a
                href={ad.landing_url}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1.5 rounded-lg border border-[var(--line)] px-3 py-1.5 text-xs text-[var(--ink-secondary)] hover:bg-[var(--panel-hover)] transition"
              >
                <ExternalLink size={12} /> Лендинг
              </a>
            )}
            {ad.cta_href && (
              <a
                href={ad.cta_href}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1.5 rounded-lg border border-[var(--line)] px-3 py-1.5 text-xs text-[var(--ink-secondary)] hover:bg-[var(--panel-hover)] transition"
              >
                <ExternalLink size={12} /> CTA ссылка
              </a>
            )}
          </div>

          {/* Topics */}
          {ad.session_topics.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {ad.session_topics.map((t) => (
                <span key={t} className="rounded-lg bg-[var(--panel-soft)] px-2 py-0.5 text-xs text-[var(--muted)]">
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value, icon: Icon, tone }: {
  label: string;
  value: number | string;
  icon: React.ElementType;
  tone?: "brand" | "success" | "warning" | "info";
}) {
  const colors = {
    brand: "bg-[var(--brand-soft)] text-[var(--brand)]",
    success: "bg-[var(--accent-soft)] text-[var(--accent)]",
    warning: "bg-[var(--warning-soft)] text-[var(--warning)]",
    info: "bg-[var(--info-soft)] text-[var(--info)]",
  };
  const cls = colors[tone ?? "brand"];
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-[var(--line)] bg-white px-5 py-4">
      <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${cls}`}>
        <Icon size={18} />
      </div>
      <div>
        <div className="text-xl font-bold text-[var(--ink)]">{value}</div>
        <div className="text-xs text-[var(--muted)]">{label}</div>
      </div>
    </div>
  );
}

export function AdsScreen() {
  const [items, setItems] = useState<EmulationHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [search, setSearch] = useState("");
  const [analysisFilter, setAnalysisFilter] = useState<AnalysisFilter>("all");
  const [mediaFilter, setMediaFilter] = useState<MediaFilter>("all");
  const [sortBy, setSortBy] = useState<SortKey>("date");
  const [filtersOpen, setFiltersOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getEmulationHistory({
        has_ads: true,
        include_captures: true,
        page_size: 100,
        page: 1,
      });
      setItems(data.items);
    } catch {
      setError("Не удалось загрузить рекламы");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // Flatten all captures from all sessions
  const allAds: AdEntry[] = useMemo(() => {
    const result: AdEntry[] = [];
    for (const item of items) {
      const caps = item.ad_captures ?? [];
      for (let i = 0; i < caps.length; i++) {
        result.push({
          ...caps[i],
          session_id: item.session_id,
          session_started_at: item.started_at ?? null,
          session_topics: item.requested_topics,
          _index: result.length,
        });
      }
    }
    return result;
  }, [items]);

  // Stats
  const stats = useMemo(() => {
    const total = allAds.length;
    const withVideo = allAds.filter((a) => a.video_file && a.video_status === "completed").length;
    const relevant = allAds.filter((a) => getAnalysisResult(a) === "relevant").length;
    const notRelevant = allAds.filter((a) => getAnalysisResult(a) === "not_relevant").length;
    return { total, withVideo, relevant, notRelevant };
  }, [allAds]);

  // Top advertisers
  const topDomains = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const ad of allAds) {
      const key = ad.advertiser_domain ?? ad.display_url ?? "unknown";
      counts[key] = (counts[key] ?? 0) + 1;
    }
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5);
  }, [allAds]);

  // Filtered & sorted
  const filtered = useMemo(() => {
    let result = allAds;

    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter(
        (a) =>
          (a.advertiser_domain ?? "").toLowerCase().includes(q) ||
          (a.headline_text ?? "").toLowerCase().includes(q) ||
          (a.display_url ?? "").toLowerCase().includes(q) ||
          a.session_topics.some((t) => t.toLowerCase().includes(q)),
      );
    }

    if (analysisFilter !== "all") {
      result = result.filter((a) => {
        const r = getAnalysisResult(a);
        if (analysisFilter === "pending") return r === null;
        return r === analysisFilter;
      });
    }

    if (mediaFilter === "video") {
      result = result.filter((a) => a.video_file && a.video_status === "completed");
    } else if (mediaFilter === "screenshot") {
      result = result.filter((a) => a.screenshot_paths.length > 0 && !(a.video_file && a.video_status === "completed"));
    }

    result = [...result].sort((a, b) => {
      if (sortBy === "date") {
        return (b.session_started_at ?? "").localeCompare(a.session_started_at ?? "");
      }
      if (sortBy === "duration") {
        return (b.ad_duration_seconds ?? 0) - (a.ad_duration_seconds ?? 0);
      }
      // domain
      return (a.advertiser_domain ?? "").localeCompare(b.advertiser_domain ?? "");
    });

    return result;
  }, [allAds, search, analysisFilter, mediaFilter, sortBy]);

  const activeFilterCount =
    (analysisFilter !== "all" ? 1 : 0) +
    (mediaFilter !== "all" ? 1 : 0) +
    (sortBy !== "date" ? 1 : 0);

  if (loading) return <Loader label="Загрузка реклам…" />;
  if (error) return (
    <div className="flex h-64 flex-col items-center justify-center gap-4">
      <p className="text-sm text-[var(--danger)]">{error}</p>
      <Button onClick={load}>Повторить</Button>
    </div>
  );

  return (
    <div className="mx-auto max-w-[1400px] space-y-6 px-4 py-6 md:px-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--ink)]">Реклама</h1>
          <p className="mt-0.5 text-sm text-[var(--muted)]">
            Все захваченные рекламные ролики по всем сессиям
          </p>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-2 rounded-xl border border-[var(--line)] bg-white px-3 py-2 text-sm text-[var(--muted)] hover:bg-[var(--panel-hover)] transition"
        >
          <RefreshCw size={14} /> Обновить
        </button>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Всего реклам" value={stats.total} icon={Megaphone} tone="brand" />
        <StatCard label="С видео" value={stats.withVideo} icon={Film} tone="info" />
        <StatCard label="Релевантных" value={stats.relevant} icon={TrendingUp} tone="success" />
        <StatCard label="Не релевантных" value={stats.notRelevant} icon={BarChart2} tone="warning" />
      </div>

      {/* Top advertisers */}
      {topDomains.length > 0 && (
        <Card>
          <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Топ рекламодателей
          </div>
          <div className="flex flex-wrap gap-2">
            {topDomains.map(([domain, count]) => (
              <button
                key={domain}
                onClick={() => setSearch(domain)}
                className="flex items-center gap-2 rounded-xl border border-[var(--line)] bg-[var(--panel-soft)] px-3 py-1.5 text-sm hover:bg-[var(--panel-hover)] transition"
              >
                <Globe size={13} className="text-[var(--muted)]" />
                <span className="font-medium text-[var(--ink)]">{domain}</span>
                <span className="rounded-full bg-[var(--brand-soft)] px-1.5 py-0.5 text-xs font-semibold text-[var(--brand)]">
                  {count}
                </span>
              </button>
            ))}
          </div>
        </Card>
      )}

      {/* Search + filters bar */}
      <div className="space-y-3">
        <div className="flex gap-2">
          {/* Search */}
          <div className="relative flex-1">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted)]" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Поиск по домену, заголовку, топику…"
              className="w-full rounded-xl border border-[var(--line)] bg-white py-2.5 pl-9 pr-9 text-sm outline-none focus:border-[var(--brand)] transition"
            />
            {search && (
              <button
                onClick={() => setSearch("")}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--muted)] hover:text-[var(--ink)]"
              >
                <X size={14} />
              </button>
            )}
          </div>

          {/* Filter toggle */}
          <button
            onClick={() => setFiltersOpen((o) => !o)}
            className={`flex items-center gap-2 rounded-xl border px-4 py-2.5 text-sm font-medium transition ${
              filtersOpen || activeFilterCount > 0
                ? "border-[var(--brand)] bg-[var(--brand-soft)] text-[var(--brand)]"
                : "border-[var(--line)] bg-white text-[var(--muted)] hover:bg-[var(--panel-hover)]"
            }`}
          >
            <SlidersHorizontal size={14} />
            Фильтры
            {activeFilterCount > 0 && (
              <span className="flex h-4 w-4 items-center justify-center rounded-full bg-[var(--brand)] text-[10px] font-bold text-white">
                {activeFilterCount}
              </span>
            )}
          </button>
        </div>

        {/* Expanded filters */}
        {filtersOpen && (
          <div className="rounded-2xl border border-[var(--line)] bg-white p-4 space-y-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              {/* Analysis */}
              <div>
                <div className="mb-1.5 text-xs font-semibold text-[var(--muted)]">Анализ</div>
                <div className="flex flex-wrap gap-1.5">
                  {(["all", "relevant", "not_relevant", "pending"] as AnalysisFilter[]).map((f) => (
                    <button
                      key={f}
                      onClick={() => setAnalysisFilter(f)}
                      className={`rounded-lg border px-3 py-1 text-xs font-medium transition ${
                        analysisFilter === f
                          ? "border-[var(--brand)] bg-[var(--brand-soft)] text-[var(--brand)]"
                          : "border-[var(--line)] text-[var(--muted)] hover:bg-[var(--panel-hover)]"
                      }`}
                    >
                      {f === "all" ? "Все" : f === "relevant" ? "Релевантные" : f === "not_relevant" ? "Не релевантные" : "Без анализа"}
                    </button>
                  ))}
                </div>
              </div>

              {/* Media */}
              <div>
                <div className="mb-1.5 text-xs font-semibold text-[var(--muted)]">Медиа</div>
                <div className="flex flex-wrap gap-1.5">
                  {(["all", "video", "screenshot"] as MediaFilter[]).map((f) => (
                    <button
                      key={f}
                      onClick={() => setMediaFilter(f)}
                      className={`rounded-lg border px-3 py-1 text-xs font-medium transition ${
                        mediaFilter === f
                          ? "border-[var(--brand)] bg-[var(--brand-soft)] text-[var(--brand)]"
                          : "border-[var(--line)] text-[var(--muted)] hover:bg-[var(--panel-hover)]"
                      }`}
                    >
                      {f === "all" ? "Все" : f === "video" ? "Только видео" : "Только скриншоты"}
                    </button>
                  ))}
                </div>
              </div>

              {/* Sort */}
              <div>
                <div className="mb-1.5 text-xs font-semibold text-[var(--muted)]">Сортировка</div>
                <div className="flex flex-wrap gap-1.5">
                  {(["date", "duration", "domain"] as SortKey[]).map((f) => (
                    <button
                      key={f}
                      onClick={() => setSortBy(f)}
                      className={`rounded-lg border px-3 py-1 text-xs font-medium transition ${
                        sortBy === f
                          ? "border-[var(--brand)] bg-[var(--brand-soft)] text-[var(--brand)]"
                          : "border-[var(--line)] text-[var(--muted)] hover:bg-[var(--panel-hover)]"
                      }`}
                    >
                      {f === "date" ? "По дате" : f === "duration" ? "По длине" : "По домену"}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {activeFilterCount > 0 && (
              <button
                onClick={() => { setAnalysisFilter("all"); setMediaFilter("all"); setSortBy("date"); }}
                className="flex items-center gap-1.5 text-xs text-[var(--muted)] hover:text-[var(--danger)] transition"
              >
                <X size={12} /> Сбросить фильтры
              </button>
            )}
          </div>
        )}
      </div>

      {/* Results count */}
      <div className="flex items-center gap-2 text-sm text-[var(--muted)]">
        <Filter size={13} />
        {filtered.length === allAds.length
          ? `${allAds.length} реклам`
          : `${filtered.length} из ${allAds.length} реклам`}
      </div>

      {/* Ad list */}
      {filtered.length === 0 ? (
        <EmptyState
          title="Рекламы не найдены"
          description={
            allAds.length === 0
              ? "Запустите эмуляцию — захваченные рекламы появятся здесь"
              : "Нет реклам, соответствующих текущим фильтрам"
          }
          action={
            allAds.length > 0 ? (
              <Button onClick={() => { setSearch(""); setAnalysisFilter("all"); setMediaFilter("all"); }}>
                Сбросить фильтры
              </Button>
            ) : undefined
          }
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-1 lg:grid-cols-2">
          {filtered.map((ad) => (
            <AdCard key={`${ad.session_id}-${ad.ad_position}-${ad._index}`} ad={ad} />
          ))}
        </div>
      )}
    </div>
  );
}
