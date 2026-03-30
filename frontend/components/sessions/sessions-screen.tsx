import { useCallback, useEffect, useMemo, useState } from "react";

import { EmptyState } from "@/components/ui/empty-state";
import { Loader } from "@/components/ui/loader";
import { getEmulationHistory, getEmulationStatusBatch } from "@/lib/api";
import type { EmulationHistoryItem, EmulationSessionStatus } from "@/types/api";
import {
  SessionFilters,
  type SessionFiltersValue,
} from "@/components/sessions/session-filters";
import { SessionTable } from "@/components/sessions/session-table";
import { Button } from "@/components/ui/button";

const initialFilters: SessionFiltersValue = {
  status: "",
  ads: "",
  search: "",
  pageSize: 10,
};

const HISTORY_POOL_PAGE_SIZE = 100;
const HISTORY_POOL_MAX_PAGES = 5;

function areItemsEquivalent(left: EmulationHistoryItem, right: EmulationHistoryItem) {
  return (
    left.status === right.status &&
    left.post_processing_status === right.post_processing_status &&
    (left.post_processing_progress?.done ?? null) === (right.post_processing_progress?.done ?? null) &&
    (left.post_processing_progress?.total ?? null) === (right.post_processing_progress?.total ?? null) &&
    left.elapsed_minutes === right.elapsed_minutes &&
    left.bytes_downloaded === right.bytes_downloaded &&
    left.total_duration_seconds === right.total_duration_seconds &&
    left.videos_watched === right.videos_watched &&
    left.watched_videos_count === right.watched_videos_count &&
    left.watched_ads_count === right.watched_ads_count &&
    left.mode === right.mode &&
    left.fatigue === right.fatigue &&
    left.error === right.error &&
    left.captures.ads_total === right.captures.ads_total &&
    left.captures.video_captures === right.captures.video_captures &&
    left.captures.screenshot_fallbacks === right.captures.screenshot_fallbacks &&
    left.topics_searched.join("\u0000") === right.topics_searched.join("\u0000")
  );
}

function mergeLiveItem(item: EmulationHistoryItem, status: EmulationSessionStatus): EmulationHistoryItem {
  const videoCaptures = status.watched_ads.filter(
    (ad) => ad.capture?.video_status === "completed",
  ).length;
  const screenshotFallbacks = status.watched_ads.filter(
    (ad) =>
      (ad.capture?.screenshot_paths?.length ?? 0) > 0 &&
      ad.capture?.video_status !== "completed",
  ).length;

  return {
    ...item,
    status: status.status,
    post_processing_status: status.post_processing_status ?? item.post_processing_status,
    post_processing_progress: status.post_processing_progress ?? item.post_processing_progress,
    elapsed_minutes: status.elapsed_minutes ?? item.elapsed_minutes,
    bytes_downloaded: status.bytes_downloaded ?? item.bytes_downloaded,
    total_duration_seconds: status.total_duration_seconds ?? item.total_duration_seconds,
    videos_watched: status.videos_watched ?? item.videos_watched,
    watched_videos_count: status.watched_videos_count ?? item.watched_videos_count,
    watched_ads_count: status.watched_ads_count ?? item.watched_ads_count,
    topics_searched: status.topics_searched ?? item.topics_searched,
    mode: status.mode ?? item.mode,
    fatigue: status.fatigue ?? item.fatigue,
    error: status.error ?? item.error,
    captures: {
      ads_total: status.watched_ads_count,
      video_captures: videoCaptures,
      screenshot_fallbacks: screenshotFallbacks,
    },
  };
}

export function SessionsScreen() {
  const [allItems, setAllItems] = useState<EmulationHistoryItem[]>([]);
  const [liveItems, setLiveItems] = useState<Record<string, EmulationHistoryItem>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState(initialFilters);
  const [page, setPage] = useState(1);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const items: EmulationHistoryItem[] = [];
      let nextPage = 1;
      let totalPages = 1;

      do {
        const data = await getEmulationHistory({
          page: nextPage,
          page_size: HISTORY_POOL_PAGE_SIZE,
          include_captures: true,
          status: filters.status || undefined,
        });
        items.push(...data.items);
        totalPages = data.total_pages;
        nextPage += 1;
      } while (nextPage <= totalPages && nextPage <= HISTORY_POOL_MAX_PAGES);

      setAllItems(items);
    } catch (err) {
      setError("Не удалось загрузить сессии.");
    } finally {
      setLoading(false);
    }
  }, [filters.status]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setPage(1);
  }, [filters.search, filters.status, filters.ads, filters.pageSize]);

  useEffect(() => {
    document
      .getElementById("app-main-scroll")
      ?.scrollTo({ top: 0, behavior: "smooth" });
  }, [page]);

  const filteredItems = useMemo(() => {
    const normalizedSearch = filters.search.trim().toLowerCase();

    return allItems.filter((item) => {
      if (filters.ads === "with_ads" && item.watched_ads_count <= 0) {
        return false;
      }
      if (filters.ads === "without_ads" && item.watched_ads_count > 0) {
        return false;
      }
      if (filters.ads === "video_captures" && item.captures.video_captures <= 0) {
        return false;
      }

      if (!normalizedSearch) {
        return true;
      }

      const haystack = [
        item.session_id,
        item.status,
        ...item.requested_topics,
        ...item.topics_searched,
      ]
        .join(" ")
        .toLowerCase();

      return haystack.includes(normalizedSearch);
    });
  }, [allItems, filters.ads, filters.search]);

  const totalPages = Math.max(1, Math.ceil(filteredItems.length / filters.pageSize));
  const currentPage = Math.min(page, totalPages);
  const paginatedItems = useMemo(() => {
    const start = (currentPage - 1) * filters.pageSize;
    return filteredItems.slice(start, start + filters.pageSize);
  }, [currentPage, filteredItems, filters.pageSize]);

  const trackedItems = useMemo(
    () => paginatedItems.map((item) => liveItems[item.session_id] ?? item),
    [liveItems, paginatedItems],
  );
  const activeItems = useMemo(
    () => trackedItems.filter((item) => ["queued", "running", "stopping"].includes(item.status)),
    [trackedItems],
  );
  const activePollKey = useMemo(
    () =>
      activeItems
        .map((item) => `${item.session_id}:${item.status}:${item.post_processing_status ?? ""}`)
        .join("|"),
    [activeItems],
  );

  useEffect(() => {
    if (activeItems.length === 0) {
      setLiveItems((prev) => (Object.keys(prev).length === 0 ? prev : {}));
      return;
    }

    let cancelled = false;

    const hydrateLive = async () => {
      const activeIds = new Set(activeItems.map((item) => item.session_id));
      let statuses: Record<string, EmulationSessionStatus> = {};
      try {
        const response = await getEmulationStatusBatch([...activeIds]);
        statuses = response.statuses;
      } catch {
        return;
      }

      if (cancelled) {
        return;
      }

      const mergedById: Record<string, EmulationHistoryItem> = {};
      for (const item of activeItems) {
        const status = statuses[item.session_id];
        if (!status) {
          continue;
        }
        mergedById[item.session_id] = mergeLiveItem(item, status);
      }

      setAllItems((prev) => {
        let changed = false;
        const next = prev.map((item) => {
          const merged = mergedById[item.session_id];
          if (!merged) {
            return item;
          }
          if (!areItemsEquivalent(item, merged)) {
            changed = true;
            return merged;
          }
          return item;
        });
        return changed ? next : prev;
      });

      setLiveItems((prev) => {
        const next: Record<string, EmulationHistoryItem> = {};
        let changed = Object.keys(prev).some((key) => !activeIds.has(key));

        for (const sessionId of activeIds) {
          if (prev[sessionId]) {
            next[sessionId] = prev[sessionId];
          }
        }

        for (const item of activeItems) {
          const merged = mergedById[item.session_id];
          if (!merged) {
            continue;
          }
          const sessionId = item.session_id;
          if (!next[sessionId] || !areItemsEquivalent(next[sessionId], merged)) {
            changed = true;
            next[sessionId] = merged;
            continue;
          }
        }

        return changed ? next : prev;
      });
    };

    void hydrateLive();
    const interval = window.setInterval(() => {
      void hydrateLive();
    }, 2500);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [activePollKey]);

  const displayedItems = trackedItems;

  const pageNumbers = useMemo(() => {
    const start = Math.max(1, currentPage - 2);
    const end = Math.min(totalPages, start + 4);
    const adjustedStart = Math.max(1, end - 4);
    return Array.from({ length: end - adjustedStart + 1 }, (_, index) => adjustedStart + index);
  }, [currentPage, totalPages]);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-[var(--ink)]">Сессии эмуляции</h2>
        <p className="mt-1 text-sm text-[var(--muted)]">Просмотр и фильтрация истории сессий</p>
      </div>

      <SessionFilters
        value={filters}
        onChange={setFilters}
        onReset={() => setFilters(initialFilters)}
      />

      {loading ? <Loader label="Загрузка сессий" /> : null}
      {!loading && error ? (
        <EmptyState title="Сессии недоступны" description={error} />
      ) : null}
      {!loading && !error && filteredItems.length === 0 ? (
        <EmptyState
          title="Ничего не найдено"
          description="Измени фильтры или запусти новую эмуляцию из дашборда."
        />
      ) : null}
      {!loading && !error && filteredItems.length > 0 ? (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-[var(--muted)]">
            <div>
              Показано {(currentPage - 1) * filters.pageSize + 1}-
              {Math.min(currentPage * filters.pageSize, filteredItems.length)} из {filteredItems.length} сессий
            </div>
            <div>В выборке: {allItems.length}</div>
          </div>
          <SessionTable items={displayedItems} />
        </>
      ) : null}

      <div className="flex items-center justify-between">
        <div className="text-xs text-[var(--muted)]">
          Страница {currentPage} из {totalPages}
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Button
            variant="ghost"
            disabled={currentPage <= 1}
            onClick={() => setPage((prev) => Math.max(1, prev - 1))}
            className="px-3 py-1.5 text-xs"
          >
            Назад
          </Button>
          {pageNumbers.map((pageNumber) => (
            <Button
              key={pageNumber}
              variant={pageNumber === currentPage ? "primary" : "ghost"}
              onClick={() => setPage(pageNumber)}
              className="px-3 py-1.5 text-xs"
            >
              {pageNumber}
            </Button>
          ))}
          <Button
            variant="ghost"
            disabled={currentPage >= totalPages}
            onClick={() => setPage((prev) => Math.min(totalPages, prev + 1))}
            className="px-3 py-1.5 text-xs"
          >
            Далее
          </Button>
        </div>
      </div>
    </div>
  );
}
