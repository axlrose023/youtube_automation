import { useCallback, useEffect, useMemo, useState } from "react";

import { EmptyState } from "@/components/ui/empty-state";
import { Loader } from "@/components/ui/loader";
import { getEmulationHistory, getEmulationStatus } from "@/lib/api";
import type { EmulationHistoryItem } from "@/types/api";
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
      setError("Failed to load sessions.");
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

  useEffect(() => {
    const activeItems = paginatedItems.filter((item) =>
      ["queued", "running", "stopping"].includes(item.status),
    );

    if (activeItems.length === 0) {
      setLiveItems((prev) => {
        if (Object.keys(prev).length === 0) {
          return prev;
        }

        const next = { ...prev };
        let changed = false;
        for (const key of Object.keys(next)) {
          if (!activeItems.some((item) => item.session_id === key)) {
            delete next[key];
            changed = true;
          }
        }
        return changed ? next : prev;
      });
      return;
    }

    let cancelled = false;

    const hydrateLive = async () => {
      const results = await Promise.all(
        activeItems.map(async (item) => {
          try {
            const status = await getEmulationStatus(item.session_id);
            const videoCaptures = status.watched_ads.filter(
              (ad) => ad.capture?.video_status === "completed",
            ).length;
            const screenshotFallbacks = status.watched_ads.filter(
              (ad) =>
                (ad.capture?.screenshot_paths?.length ?? 0) > 0 &&
                ad.capture?.video_status !== "completed",
            ).length;

            const merged: EmulationHistoryItem = {
              ...item,
              status: status.status,
              elapsed_minutes: status.elapsed_minutes ?? item.elapsed_minutes,
              bytes_downloaded: status.bytes_downloaded ?? item.bytes_downloaded,
              total_duration_seconds: status.total_duration_seconds ?? item.total_duration_seconds,
              videos_watched: status.videos_watched ?? item.videos_watched,
              watched_videos_count: status.watched_videos_count ?? item.watched_videos_count,
              watched_ads_count: status.watched_ads_count ?? item.watched_ads_count,
              topics_searched: status.topics_searched ?? item.topics_searched,
              watched_videos: status.watched_videos ?? item.watched_videos,
              watched_ads: status.watched_ads ?? item.watched_ads,
              watched_ads_analytics: status.watched_ads_analytics ?? item.watched_ads_analytics,
              mode: status.mode ?? item.mode,
              fatigue: status.fatigue ?? item.fatigue,
              error: status.error ?? item.error,
              captures: {
                ads_total: status.watched_ads_count,
                video_captures: videoCaptures,
                screenshot_fallbacks: screenshotFallbacks,
              },
            };

            return [item.session_id, merged] as const;
          } catch {
            return null;
          }
        }),
      );

      if (cancelled) {
        return;
      }

      setLiveItems((prev) => {
        const next = { ...prev };
        let changed = false;

        for (const result of results) {
          if (!result) {
            continue;
          }
          const [sessionId, merged] = result;
          next[sessionId] = merged;
          changed = true;
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
  }, [paginatedItems]);

  const displayedItems = useMemo(
    () => paginatedItems.map((item) => liveItems[item.session_id] ?? item),
    [liveItems, paginatedItems],
  );

  const pageNumbers = useMemo(() => {
    const start = Math.max(1, currentPage - 2);
    const end = Math.min(totalPages, start + 4);
    const adjustedStart = Math.max(1, end - 4);
    return Array.from({ length: end - adjustedStart + 1 }, (_, index) => adjustedStart + index);
  }, [currentPage, totalPages]);

  return (
    <div className="space-y-6">
      <section className="panel relative overflow-hidden p-6">
        <div className="absolute right-0 top-0 h-28 w-28 rounded-full bg-[radial-gradient(circle,rgba(214,82,82,0.14),transparent_68%)]" />
        <div className="relative">
          <div className="section-eyebrow">History</div>
          <h2 className="mt-2 text-2xl font-semibold text-[var(--ink)]">Emulation sessions</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--muted)]">
            Filter past runs, watch active sessions update in place, and jump directly into
            runtime detail when you need deeper inspection.
          </p>
        </div>
      </section>

      <SessionFilters
        value={filters}
        onChange={setFilters}
        onReset={() => setFilters(initialFilters)}
      />

      {loading ? <Loader label="Loading sessions" /> : null}
      {!loading && error ? (
        <EmptyState title="Sessions unavailable" description={error} />
      ) : null}
      {!loading && !error && filteredItems.length === 0 ? (
        <EmptyState
          title="No sessions matched"
          description="Adjust the filters or start a new emulation from the dashboard."
        />
      ) : null}
      {!loading && !error && filteredItems.length > 0 ? (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-[var(--muted)]">
            <div>
              Showing {(currentPage - 1) * filters.pageSize + 1}-
              {Math.min(currentPage * filters.pageSize, filteredItems.length)} of {filteredItems.length} sessions
            </div>
            <div>Loaded pool: {allItems.length}</div>
          </div>
          <SessionTable items={displayedItems} />
        </>
      ) : null}

      <div className="flex items-center justify-between">
        <div className="text-sm text-[var(--muted)]">
          Page {currentPage} of {totalPages}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="ghost"
            disabled={currentPage <= 1}
            onClick={() => setPage((prev) => Math.max(1, prev - 1))}
          >
            Prev
          </Button>
          {pageNumbers.map((pageNumber) => (
            <Button
              key={pageNumber}
              variant={pageNumber === currentPage ? "primary" : "ghost"}
              onClick={() => setPage(pageNumber)}
            >
              {pageNumber}
            </Button>
          ))}
          <Button
            variant="ghost"
            disabled={currentPage >= totalPages}
            onClick={() => setPage((prev) => Math.min(totalPages, prev + 1))}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  );
}
