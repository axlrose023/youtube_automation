import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Clock,
  Film,
  Image,
  Loader2,
  Sparkles,
  X,
} from "lucide-react";

import { SessionLauncher } from "@/components/dashboard/session-launcher";
import { EmptyState } from "@/components/ui/empty-state";
import { Loader } from "@/components/ui/loader";
import { apiClient } from "@/lib/api-client";
import { getDashboardSummary, getEmulationHistory } from "@/lib/api";
import { formatSessionStatus } from "@/lib/metrics";
import { formatDate, formatMinutes } from "@/lib/format";
import type { EmulationDashboardSummary, EmulationHistoryItem, EmulationAdCapture } from "@/types/api";

// ─── Country map ─────────────────────────────────────────────────────────────

const COUNTRIES: Record<string, { flag: string; name: string }> = {
  FR: { flag: "🇫🇷", name: "Франция" },
  DE: { flag: "🇩🇪", name: "Германия" },
  US: { flag: "🇺🇸", name: "США" },
  GB: { flag: "🇬🇧", name: "Великобритания" },
  IT: { flag: "🇮🇹", name: "Италия" },
  ES: { flag: "🇪🇸", name: "Испания" },
  NL: { flag: "🇳🇱", name: "Нидерланды" },
  PL: { flag: "🇵🇱", name: "Польша" },
  SE: { flag: "🇸🇪", name: "Швеция" },
  NO: { flag: "🇳🇴", name: "Норвегия" },
  CH: { flag: "🇨🇭", name: "Швейцария" },
  AT: { flag: "🇦🇹", name: "Австрия" },
  BE: { flag: "🇧🇪", name: "Бельгия" },
  IE: { flag: "🇮🇪", name: "Ирландия" },
  CA: { flag: "🇨🇦", name: "Канада" },
  AU: { flag: "🇦🇺", name: "Австралия" },
  JP: { flag: "🇯🇵", name: "Япония" },
  UA: { flag: "🇺🇦", name: "Украина" },
  TR: { flag: "🇹🇷", name: "Турция" },
};

function countryOf(code: string | null | undefined): { flag: string; name: string } {
  if (!code) return { flag: "🏳️", name: "—" };
  return COUNTRIES[code.toUpperCase()] ?? { flag: "🏳️", name: code };
}

// ─── Status config ────────────────────────────────────────────────────────────

const STATUS_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  completed: { label: "Завершена",       color: "var(--accent)",  bg: "var(--accent-soft)" },
  running:   { label: "Активная",        color: "var(--info)",    bg: "rgba(9,132,227,0.10)" },
  queued:    { label: "В очереди",       color: "var(--warning)", bg: "rgba(243,156,18,0.10)" },
  stopping:  { label: "Останавливается", color: "var(--warning)", bg: "rgba(243,156,18,0.10)" },
  failed:    { label: "Ошибка",          color: "var(--danger)",  bg: "rgba(231,76,60,0.10)" },
  stopped:   { label: "Остановлена",     color: "var(--muted)",   bg: "var(--bg-soft)" },
};

// ─── Analysis helpers ─────────────────────────────────────────────────────────

function getAdResult(capture: EmulationAdCapture): "relevant" | "not_relevant" | null {
  const r = capture.analysis_summary?.["result"];
  if (r === "relevant" || r === "not_relevant") return r;
  if (capture.analysis_status === "completed") return "relevant";
  if (capture.analysis_status === "not_relevant") return "not_relevant";
  return null;
}

function buildMediaPath(value: string | null | undefined) {
  if (!value) return null;
  const normalized = value.replace(/\\/g, "/").replace(/^\.\//, "");
  const isAbsolute = normalized.startsWith("/");
  const encoded = normalized.split("/").filter(Boolean).map((s) => encodeURIComponent(s)).join("/");
  return `/emulation/media/${isAbsolute ? "/" : ""}${encoded}`;
}

// ─── Tiny shared primitives ───────────────────────────────────────────────────

function Label({ children }: { children: React.ReactNode }) {
  return <div className="text-[11px] uppercase tracking-wider font-semibold" style={{ color: "var(--muted)" }}>{children}</div>;
}

function BigNum({ children, color }: { children: React.ReactNode; color?: string }) {
  return <div className="mt-0.5 text-[26px] font-semibold tabular-nums leading-none" style={{ color: color ?? "var(--ink)" }}>{children}</div>;
}

function Sub({ children }: { children: React.ReactNode }) {
  return <div className="mt-1 text-[12px]" style={{ color: "var(--muted)" }}>{children}</div>;
}

// ─── 1. Summary strip ─────────────────────────────────────────────────────────

function SummaryStrip({ s }: { s: EmulationDashboardSummary }) {
  const relPct = Math.round((s.relevant_ads / Math.max(1, s.total_ad_captures)) * 100);
  const analyzedPct = Math.round((s.analyzed_ads / Math.max(1, s.total_ad_captures)) * 100);
  const pending = s.total_ad_captures - s.analyzed_ads;
  const hitPct = Math.round((s.relevant_ads / Math.max(1, s.analyzed_ads)) * 100);

  const cells = [
    {
      key: "sessions",
      content: (
        <>
          <Label>Сессии</Label>
          <BigNum>{s.total_sessions}</BigNum>
          <Sub>
            <span className="font-semibold" style={{ color: "var(--accent)" }}>{s.completed}</span>
            {" завершено · "}
            <span className="font-semibold" style={{ color: "var(--info)" }}>{s.running}</span>
            {" активных"}
          </Sub>
        </>
      ),
    },
    {
      key: "captured",
      content: (
        <>
          <Label>Реклама захвачена</Label>
          <BigNum>{s.total_ad_captures}</BigNum>
          <div className="mt-2 h-1 rounded-full overflow-hidden" style={{ background: "var(--bg-soft)" }}>
            <div className="h-full rounded-full transition-all" style={{ width: `${relPct}%`, background: "var(--accent)" }} />
          </div>
          <Sub><span className="tabular-nums">{relPct}%</span> релевантных от захваченных</Sub>
        </>
      ),
    },
    {
      key: "relevant",
      content: (
        <>
          <Label>Релевантная реклама</Label>
          <div className="mt-0.5 flex items-baseline gap-2">
            <BigNum color="var(--accent)">{s.relevant_ads}</BigNum>
            <div className="text-[12px] tabular-nums" style={{ color: "var(--muted)" }}>из {s.analyzed_ads}</div>
          </div>
          <Sub>
            <span className="font-semibold" style={{ color: "var(--ink-secondary)" }}>{hitPct}%</span>
            {" успешных попаданий"}
          </Sub>
        </>
      ),
    },
    {
      key: "analysis",
      content: (
        <>
          <Label>Анализ</Label>
          <div className="mt-0.5 flex items-baseline gap-2">
            <BigNum>{s.analyzed_ads}</BigNum>
            <div className="text-[12px] tabular-nums" style={{ color: "var(--muted)" }}>из {s.total_ad_captures}</div>
          </div>
          {pending > 0 ? (
            <div className="mt-1 flex items-center gap-1.5">
              <span className="inline-flex items-center gap-0.5">
                {[0, 1, 2].map((i) => (
                  <span key={i} className="w-1 h-1 rounded-full" style={{ background: "var(--info)", animation: `blink 1.4s ${i * 0.2}s infinite` }} />
                ))}
              </span>
              <span className="text-[12px]" style={{ color: "var(--muted)" }}>
                В очереди <span className="font-semibold tabular-nums" style={{ color: "var(--info)" }}>{pending}</span>
              </span>
            </div>
          ) : (
            <Sub><span className="font-semibold" style={{ color: "var(--accent)" }}>Очередь пуста</span></Sub>
          )}
        </>
      ),
    },
  ];

  return (
    <div className="rounded-2xl flex items-stretch flex-wrap" style={{ background: "var(--panel)", boxShadow: "inset 0 0 0 1px var(--line)" }}>
      {cells.map((c, i) => (
        <div key={c.key} className="flex-1 min-w-[150px] px-5 py-4" style={{ borderLeft: i === 0 ? "none" : "1px solid var(--line)" }}>
          {c.content}
        </div>
      ))}
    </div>
  );
}

// ─── 2. Activity heatmap (last 14 days derived from sessions) ─────────────────

function ActivityHeatmap({ items }: { items: EmulationHistoryItem[] }) {
  const days = useMemo(() => {
    const DAY_LABELS = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    // Build count per day offset (0 = today, 13 = 13 days ago)
    const counts = new Array(14).fill(0);
    for (const item of items) {
      const d = new Date(item.queued_at);
      d.setHours(0, 0, 0, 0);
      const diff = Math.round((today.getTime() - d.getTime()) / 86400000);
      if (diff >= 0 && diff < 14) counts[13 - diff]++;
    }

    return counts.map((n, i) => {
      const d = new Date(today);
      d.setDate(today.getDate() - (13 - i));
      return {
        count: n,
        day: DAY_LABELS[d.getDay()],
        date: `${d.getDate()}.${String(d.getMonth() + 1).padStart(2, "0")}`,
        isToday: i === 13,
      };
    });
  }, [items]);

  const total = days.reduce((a, b) => a + b.count, 0);

  const colorFor = (n: number) => {
    if (n === 0) return "var(--bg-soft)";
    if (n === 1) return "var(--brand-soft)";
    if (n <= 3) return "rgba(108,92,231,0.45)";
    return "var(--brand)";
  };

  return (
    <div className="rounded-2xl p-5 flex items-center gap-6 flex-wrap" style={{ background: "var(--panel)", boxShadow: "inset 0 0 0 1px var(--line)" }}>
      <div className="min-w-[160px]">
        <div className="text-[11px] uppercase tracking-wider font-semibold" style={{ color: "var(--muted)" }}>Активность · 14 дней</div>
        <div className="mt-1 flex items-baseline gap-2">
          <div className="text-[22px] font-semibold tabular-nums leading-none" style={{ color: "var(--ink)" }}>{total}</div>
          <div className="text-[12px]" style={{ color: "var(--muted)" }}>сессий запущено</div>
        </div>
      </div>

      <div className="flex-1 min-w-[360px]">
        <div className="flex items-end gap-1.5">
          {days.map((d, i) => (
            <div key={i} className="flex flex-col items-center gap-1.5 group" style={{ flex: 1, minWidth: 18 }}>
              <div className="relative">
                <div
                  className="w-full aspect-square rounded-md transition-transform group-hover:scale-105"
                  style={{
                    background: colorFor(d.count),
                    boxShadow: d.isToday ? "inset 0 0 0 2px var(--ink)" : "inset 0 0 0 1px var(--line)",
                    minWidth: 20, minHeight: 20,
                  }}
                  title={`${d.date} · ${d.count} сессий`}
                />
                <div className="pointer-events-none absolute -top-9 left-1/2 -translate-x-1/2 whitespace-nowrap text-[11px] px-1.5 py-0.5 rounded-md text-white opacity-0 group-hover:opacity-100 transition-opacity" style={{ background: "var(--ink)" }}>
                  {d.date} · {d.count}
                </div>
              </div>
              <div className="text-[10.5px] tabular-nums" style={{ color: d.isToday ? "var(--ink)" : "var(--muted)", fontWeight: d.isToday ? 600 : 400 }}>
                {d.day}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-1.5">
        <span className="text-[11px]" style={{ color: "var(--muted)" }}>меньше</span>
        {[0, 1, 2, 4].map((n, i) => (
          <span key={i} className="w-3 h-3 rounded-sm" style={{ background: colorFor(n), boxShadow: "inset 0 0 0 1px var(--line)" }} />
        ))}
        <span className="text-[11px]" style={{ color: "var(--muted)" }}>больше</span>
      </div>
    </div>
  );
}

// ─── 3. Recent sessions table ─────────────────────────────────────────────────

function RecentSessionsTable({ items }: { items: EmulationHistoryItem[] }) {
  return (
    <div className="rounded-2xl overflow-hidden flex flex-col" style={{ background: "var(--panel)", boxShadow: "inset 0 0 0 1px var(--line)" }}>
      <div className="px-5 pt-4 pb-3 flex items-center justify-between border-b" style={{ borderColor: "var(--line)" }}>
        <div className="flex items-center gap-2">
          <h3 className="text-[15px] font-semibold leading-none" style={{ color: "var(--ink)" }}>Последние сессии</h3>
          <span className="inline-flex items-center h-6 px-2 rounded-full text-[11.5px] font-medium tabular-nums" style={{ background: "var(--panel-soft)", boxShadow: "inset 0 0 0 1px var(--line)", color: "var(--ink-secondary)" }}>
            {items.length}
          </span>
        </div>
        <Link to="/sessions" className="inline-flex items-center gap-1 h-7 px-2.5 rounded-full text-[12px] font-medium hover:opacity-80" style={{ color: "var(--ink-secondary)" }}>
          Все сессии <ChevronRight size={12} />
        </Link>
      </div>

      {/* Column headers */}
      <div
        className="grid items-center px-5 py-2.5 text-[11px] uppercase tracking-wider font-semibold"
        style={{ gridTemplateColumns: "1.6fr 1fr 0.9fr 0.6fr", borderBottom: "1px solid var(--line)", color: "var(--muted)" }}
      >
        <div>Статус</div>
        <div>Гео</div>
        <div className="text-right">Длительность</div>
        <div className="text-right">Реклама</div>
      </div>

      <ul className="divide-y" style={{ borderColor: "var(--line)" }}>
        {items.map((item) => {
          const st = STATUS_CONFIG[item.status] ?? STATUS_CONFIG["stopped"];
          const country = item.proxy_country_code ?? null;
          const c = countryOf(country);
          const adsTotal = item.captures?.ads_total ?? 0;
          const relevantAds = (item.watched_ads_analytics ?? []).filter((a) => !a.skip_clicked).length;

          return (
            <Link
              key={item.session_id}
              to={`/sessions/${item.session_id}`}
              className="relative grid items-center px-5 py-3.5 transition-colors"
              style={{ gridTemplateColumns: "1.6fr 1fr 0.9fr 0.6fr", display: "grid", height: 56 }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel-soft)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "")}
            >
              {/* Colored left border by status */}
              <span className="absolute left-0 top-2 bottom-2 w-[3px] rounded-r" style={{ background: st.color }} />

              {/* Status pill */}
              <div className="min-w-0">
                <span
                  className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-full text-[12px] font-medium"
                  style={{ background: st.bg, color: st.color }}
                >
                  {item.status === "running" && <Loader2 size={12} strokeWidth={2.4} className="animate-spin" />}
                  {item.status === "completed" && <Check size={12} strokeWidth={2.4} />}
                  {item.status === "failed" && <X size={12} strokeWidth={2.4} />}
                  {(item.status === "queued" || item.status === "stopping") && <Clock size={12} strokeWidth={2.4} />}
                  {st.label}
                </span>
              </div>

              {/* Geo only */}
              <div className="min-w-0 flex items-center gap-2">
                {country ? (
                  <>
                    <span className="text-[16px] leading-none">{c.flag}</span>
                    <span className="text-[13px] font-medium truncate" style={{ color: "var(--ink-secondary)" }}>{c.name}</span>
                  </>
                ) : (
                  <span className="text-[13px]" style={{ color: "var(--muted)" }}>—</span>
                )}
              </div>

              {/* Duration */}
              <div className="text-[12.5px] tabular-nums text-right" style={{ color: "var(--ink-secondary)" }}>
                {item.elapsed_minutes != null ? `${Math.round(item.elapsed_minutes)} мин` : "—"}
              </div>

              {/* Ads count + relevant chip */}
              <div className="flex items-center justify-end gap-1.5">
                <span className="inline-flex items-center gap-1 h-7 px-2.5 rounded-full text-[12px] font-semibold tabular-nums" style={{ background: "var(--panel-soft)", boxShadow: "inset 0 0 0 1px var(--line)", color: "var(--ink)" }}>
                  <Image size={12} style={{ color: "var(--muted)" }} /> {adsTotal}
                </span>
                {relevantAds > 0 && (
                  <span className="inline-flex items-center gap-0.5 h-6 px-1.5 rounded-full text-[11px] font-semibold tabular-nums" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>
                    <Check size={10} strokeWidth={2.5} />{relevantAds}
                  </span>
                )}
              </div>
            </Link>
          );
        })}
      </ul>
    </div>
  );
}

// ─── 4. Geo breakdown ─────────────────────────────────────────────────────────

function GeoBreakdown({ items }: { items: EmulationHistoryItem[] }) {
  const rows = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const item of items) {
      const code = item.proxy_country_code ?? null;
      if (code) counts[code] = (counts[code] || 0) + 1;
    }
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
      .map(([code, n]) => ({ code, n }));
  }, [items]);

  const total = rows.reduce((a, b) => a + b.n, 0);
  const max = Math.max(...rows.map((r) => r.n), 1);

  if (rows.length === 0) {
    return (
      <div className="rounded-2xl p-5 flex flex-col" style={{ background: "var(--panel)", boxShadow: "inset 0 0 0 1px var(--line)" }}>
        <h3 className="text-[15px] font-semibold leading-none mb-4" style={{ color: "var(--ink)" }}>География</h3>
        <div className="flex-1 flex items-center justify-center text-[13px]" style={{ color: "var(--muted)" }}>Нет данных о гео</div>
      </div>
    );
  }

  return (
    <div className="rounded-2xl p-5 flex flex-col" style={{ background: "var(--panel)", boxShadow: "inset 0 0 0 1px var(--line)" }}>
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-[15px] font-semibold leading-none" style={{ color: "var(--ink)" }}>География</h3>
        <span className="text-[11.5px] tabular-nums" style={{ color: "var(--muted)" }}>{total} сессий</span>
      </div>
      <ul className="flex flex-col gap-2.5">
        {rows.map(({ code, n }) => {
          const c = countryOf(code);
          const pct = (n / max) * 100;
          return (
            <li key={code} className="flex items-center gap-3">
              <span className="text-[16px] leading-none w-5 text-center">{c.flag}</span>
              <span className="flex-1 min-w-0 text-[13px] truncate" style={{ color: "var(--ink-secondary)" }}>{c.name}</span>
              <span className="w-[45%] h-1.5 rounded-full overflow-hidden" style={{ background: "var(--bg-soft)" }}>
                <span className="block h-full rounded-full" style={{ width: `${pct}%`, background: "var(--brand)" }} />
              </span>
              <span className="w-7 text-right text-[12.5px] tabular-nums font-semibold" style={{ color: "var(--ink)" }}>{n}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ─── 5. Recent ads strip ──────────────────────────────────────────────────────

function AdResultPill({ capture }: { capture: EmulationAdCapture }) {
  const result = getAdResult(capture);
  const isPending = !result && (capture.analysis_status === "pending" || capture.analysis_status === "queued");

  if (result === "relevant")
    return <span className="inline-flex items-center gap-1 h-6 px-2 rounded-full text-[11.5px] font-medium" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}><Check size={11} strokeWidth={2.5} /> Релевантно</span>;
  if (result === "not_relevant")
    return <span className="inline-flex items-center gap-1 h-6 px-2 rounded-full text-[11.5px] font-medium" style={{ background: "var(--bg-soft)", color: "var(--ink-secondary)" }}>Нерелевантно</span>;
  if (isPending)
    return <span className="inline-flex items-center gap-1 h-6 px-2 rounded-full text-[11.5px] font-medium" style={{ background: "rgba(9,132,227,0.08)", color: "#0984e3" }}><Sparkles size={11} strokeWidth={2} /> Анализ…</span>;
  return <span className="inline-flex items-center gap-1 h-6 px-2 rounded-full text-[11.5px] font-medium" style={{ background: "var(--panel-soft)", color: "var(--muted)" }}>Нет данных</span>;
}

function AdThumbnailSmall({ capture, sessionId }: { capture: EmulationAdCapture; sessionId: string }) {
  const firstShot = capture.screenshot_paths?.[0];
  const shotUrl = firstShot ? buildMediaPath(firstShot.file_path) : null;
  const hasVideo = Boolean(capture.video_file && capture.video_status === "completed");
  const [failed, setFailed] = useState(false);

  if (shotUrl && !failed) {
    return (
      <div className="relative w-full overflow-hidden rounded-xl" style={{ aspectRatio: "16/10", background: "var(--panel-soft)" }}>
        <img src={shotUrl} alt="" className="w-full h-full object-contain" loading="lazy" onError={() => setFailed(true)} />
        {hasVideo && (
          <div className="absolute left-2 bottom-2 flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-semibold text-white" style={{ background: "rgba(0,0,0,0.55)" }}>
            <Film size={8} /> Видео
          </div>
        )}
      </div>
    );
  }
  // Gradient placeholder keyed by ad position (deterministic hue)
  const hue = ((capture.ad_position ?? 0) * 47 + 200) % 360;
  const bg = `linear-gradient(135deg, oklch(0.92 0.04 ${hue}) 0%, oklch(0.85 0.07 ${(hue + 30) % 360}) 100%)`;
  return (
    <div className="relative w-full rounded-xl overflow-hidden" style={{ aspectRatio: "16/10", background: bg, backgroundImage: `${bg}, repeating-linear-gradient(135deg, rgba(0,0,0,0.025) 0 8px, transparent 8px 16px)` }}>
      <div className="absolute top-0 left-0 right-0 h-6 flex items-center gap-1 px-2" style={{ background: "rgba(255,255,255,0.55)" }}>
        <span className="w-1.5 h-1.5 rounded-full" style={{ background: "rgba(0,0,0,0.18)" }} />
        <span className="w-1.5 h-1.5 rounded-full" style={{ background: "rgba(0,0,0,0.12)" }} />
        <div className="ml-2 h-3 flex-1 rounded-sm" style={{ background: "rgba(0,0,0,0.06)" }} />
      </div>
      <div className="absolute inset-0 pt-7 px-3 pb-3 flex flex-col justify-end">
        <div className="rounded-md p-1.5" style={{ background: "rgba(255,255,255,0.78)" }}>
          <div className="text-[9px] uppercase tracking-wider font-semibold" style={{ color: "var(--muted)" }}>Ad</div>
          <div className="text-[10px] font-semibold truncate" style={{ color: "var(--ink)" }}>{capture.advertiser_domain ?? "—"}</div>
          {capture.headline_text && <div className="text-[9px] line-clamp-1" style={{ color: "var(--ink-secondary)" }}>{capture.headline_text}</div>}
        </div>
      </div>
    </div>
  );
}

function RecentAdsStrip({ items }: { items: EmulationHistoryItem[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);

  const recentAds = useMemo(() => {
    const pairs: Array<{ capture: EmulationAdCapture; sessionId: string }> = [];
    for (const item of [...items].reverse()) {
      for (const cap of (item.ad_captures ?? []).slice().reverse()) {
        pairs.push({ capture: cap, sessionId: item.session_id });
        if (pairs.length >= 8) break;
      }
      if (pairs.length >= 8) break;
    }
    return pairs.reverse();
  }, [items]);

  const scrollBy = (dir: number) => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollBy({ left: dir * (el.clientWidth - 64), behavior: "smooth" });
  };

  return (
    <div className="rounded-2xl flex flex-col" style={{ background: "var(--panel)", boxShadow: "inset 0 0 0 1px var(--line)" }}>
      <div className="px-5 pt-4 pb-3 flex items-center justify-between border-b" style={{ borderColor: "var(--line)" }}>
        <div className="flex items-center gap-2">
          <h3 className="text-[15px] font-semibold leading-none" style={{ color: "var(--ink)" }}>Недавняя реклама</h3>
          <span className="inline-flex items-center h-6 px-2 rounded-full text-[11.5px] font-medium tabular-nums" style={{ background: "var(--panel-soft)", boxShadow: "inset 0 0 0 1px var(--line)", color: "var(--ink-secondary)" }}>
            {recentAds.length}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button onClick={() => scrollBy(-1)} className="h-7 w-7 grid place-items-center rounded-lg hover:opacity-80" style={{ boxShadow: "inset 0 0 0 1px var(--line)", background: "var(--panel-soft)", color: "var(--ink-secondary)" }}>
            <ChevronLeft size={14} />
          </button>
          <button onClick={() => scrollBy(1)} className="h-7 w-7 grid place-items-center rounded-lg hover:opacity-80" style={{ boxShadow: "inset 0 0 0 1px var(--line)", background: "var(--panel-soft)", color: "var(--ink-secondary)" }}>
            <ChevronRight size={14} />
          </button>
          <Link to="/ads" className="ml-1 inline-flex items-center gap-1 h-7 px-2.5 rounded-full text-[12px] font-medium hover:opacity-80" style={{ color: "var(--ink-secondary)" }}>
            Вся реклама <ChevronRight size={12} />
          </Link>
        </div>
      </div>

      {recentAds.length === 0 ? (
        <div className="flex-1 flex items-center justify-center py-10 text-[13px]" style={{ color: "var(--muted)" }}>
          Нет захваченной рекламы
        </div>
      ) : (
        <div
          ref={scrollRef}
          className="flex gap-3 overflow-x-auto px-4 py-4"
          style={{ scrollbarWidth: "thin" }}
        >
          {recentAds.map(({ capture, sessionId }) => (
            <Link
              key={`${sessionId}-${capture.ad_position}`}
              to={`/sessions/${sessionId}`}
              className="shrink-0 flex flex-col gap-2 p-2 rounded-2xl transition-shadow hover:shadow-md"
              style={{ width: 220, minWidth: 220, background: "var(--panel)", boxShadow: "inset 0 0 0 1px var(--line)" }}
            >
              <AdThumbnailSmall capture={capture} sessionId={sessionId} />
              <div className="px-1 flex flex-col gap-0.5">
                <div className="text-[13px] font-semibold truncate" style={{ color: "var(--ink)" }}>
                  {capture.advertiser_domain ?? "Неизвестный"}
                </div>
                {capture.headline_text && (
                  <div className="text-[11.5px] line-clamp-1" style={{ color: "var(--muted)" }}>{capture.headline_text}</div>
                )}
              </div>
              <div className="px-1 flex items-center justify-between gap-2 mt-auto">
                <AdResultPill capture={capture} />
                {capture.ad_duration_seconds != null && (
                  <span className="inline-flex items-center gap-1 text-[11.5px] tabular-nums" style={{ color: "var(--muted)" }}>
                    <Clock size={11} /> {capture.ad_duration_seconds.toFixed(0)}с
                  </span>
                )}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Main screen ──────────────────────────────────────────────────────────────

export function DashboardScreen() {
  const [items, setItems] = useState<EmulationHistoryItem[]>([]);
  const [summary, setSummary] = useState<EmulationDashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [summaryData, historyData] = await Promise.all([
        getDashboardSummary(),
        getEmulationHistory({ page: 1, page_size: 50, include_captures: true }),
      ]);
      setSummary(summaryData);
      setItems(historyData.items);
    } catch {
      setError("Не удалось загрузить данные дашборда.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  if (loading) return <Loader label="Загрузка дашборда…" />;
  if (error || !summary) return <EmptyState title="Дашборд недоступен" description={error ?? "Данные не получены."} />;

  const recentItems = items.slice(0, 8);

  return (
    <>
      <style>{`@keyframes blink { 0%, 60%, 100% { opacity: 0.25 } 30% { opacity: 1 } }`}</style>

      <div className="flex flex-col" style={{ gap: 20 }}>
        <SummaryStrip s={summary} />
        <ActivityHeatmap items={items} />

        <div className="grid grid-cols-1 lg:grid-cols-5" style={{ gap: 20 }}>
          <div className="lg:col-span-3">
            <RecentSessionsTable items={recentItems} />
          </div>
          <div className="lg:col-span-2">
            <SessionLauncher popularTopics={summary.top_topics.map((t) => t.label)} />
          </div>
        </div>

        <div className="grid grid-cols-1" style={{ gridTemplateColumns: "35fr 65fr", gap: 20 }}>
          <GeoBreakdown items={items} />
          <RecentAdsStrip items={items} />
        </div>
      </div>
    </>
  );
}
