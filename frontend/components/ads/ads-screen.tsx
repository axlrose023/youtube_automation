import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Clock,
  ExternalLink,
  Film,
  Globe,
  Image,
  RefreshCw,
  Search,
  Sparkles,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Loader } from "@/components/ui/loader";
import { apiClient } from "@/lib/api-client";
import { getEmulationHistory } from "@/lib/api";
import { getPreferredAdScreenshot } from "@/lib/ad-screenshots";
import { formatDate } from "@/lib/format";
import type { EmulationAdCapture, EmulationHistoryItem } from "@/types/api";

// ─── Types ────────────────────────────────────────────────────────────────────

type AdEntry = EmulationAdCapture & {
  session_id: string;
  session_started_at: string | null;
  session_topics: string[];
  session_proxy_country: string | null;
  _index: number;
};

type SessionGroup = {
  session_id: string;
  session_started_at: string | null;
  session_topics: string[];
  session_proxy_country: string | null;
  ads: AdEntry[];
};

type AnalysisFilter = "all" | "relevant" | "not_relevant" | "pending";

// ─── Country data ─────────────────────────────────────────────────────────────

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
  FI: { flag: "🇫🇮", name: "Финляндия" },
  DK: { flag: "🇩🇰", name: "Дания" },
  PT: { flag: "🇵🇹", name: "Португалия" },
  CH: { flag: "🇨🇭", name: "Швейцария" },
  AT: { flag: "🇦🇹", name: "Австрия" },
  BE: { flag: "🇧🇪", name: "Бельгия" },
  IE: { flag: "🇮🇪", name: "Ирландия" },
  CA: { flag: "🇨🇦", name: "Канада" },
  AU: { flag: "🇦🇺", name: "Австралия" },
  JP: { flag: "🇯🇵", name: "Япония" },
  BR: { flag: "🇧🇷", name: "Бразилия" },
  UA: { flag: "🇺🇦", name: "Украина" },
  RU: { flag: "🇷🇺", name: "Россия" },
  CZ: { flag: "🇨🇿", name: "Чехия" },
  SK: { flag: "🇸🇰", name: "Словакия" },
  HU: { flag: "🇭🇺", name: "Венгрия" },
  RO: { flag: "🇷🇴", name: "Румыния" },
  BG: { flag: "🇧🇬", name: "Болгария" },
  HR: { flag: "🇭🇷", name: "Хорватия" },
  GR: { flag: "🇬🇷", name: "Греция" },
  TR: { flag: "🇹🇷", name: "Турция" },
  IN: { flag: "🇮🇳", name: "Индия" },
  SG: { flag: "🇸🇬", name: "Сингапур" },
  KR: { flag: "🇰🇷", name: "Корея" },
  CN: { flag: "🇨🇳", name: "Китай" },
  MX: { flag: "🇲🇽", name: "Мексика" },
  AR: { flag: "🇦🇷", name: "Аргентина" },
  ZA: { flag: "🇿🇦", name: "ЮАР" },
  IL: { flag: "🇮🇱", name: "Израиль" },
};

function countryOf(code: string | null | undefined): { flag: string; name: string } {
  if (!code) return { flag: "🏳️", name: code ?? "—" };
  return COUNTRIES[code.toUpperCase()] ?? { flag: "🏳️", name: code };
}

// ─── Domain helpers ──────────────────────────────────────────────────────────

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
    const host = u.hostname.replace(/^www\./, "");
    if (_REDIRECT_HOSTS.has(u.hostname)) return null;
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
    extractCleanDomain(ad.landing_url) ??
    extractCleanDomain(ad.advertiser_domain) ??
    extractCleanDomain(ad.display_url) ??
    null;
  const headline = ad.headline_text ?? "";
  const isChannelHeadline = /^(subscribe to|подпишитесь на)/i.test(headline.trim());
  const name = summaryName ?? domain ?? (isChannelHeadline ? "" : headline) ?? "Неизвестный рекламодатель";
  return { name: name || "Неизвестный рекламодатель", domain };
}

// ─── Analysis helpers ─────────────────────────────────────────────────────────

function getAnalysisResult(capture: EmulationAdCapture): "relevant" | "not_relevant" | null {
  const r = capture.analysis_summary?.["result"];
  if (r === "relevant" || r === "not_relevant") return r;
  if (capture.analysis_status === "completed") return "relevant";
  if (capture.analysis_status === "not_relevant") return "not_relevant";
  return null;
}

function getResultKey(ad: AdEntry): "relevant" | "not_relevant" | "pending" {
  const r = getAnalysisResult(ad);
  if (r === "relevant") return "relevant";
  if (r === "not_relevant") return "not_relevant";
  return "pending";
}

// ─── Media helpers ─────────────────────────────────────────────────────────────

function buildMediaPath(value: string | null | undefined) {
  if (!value) return null;
  const normalized = value.replace(/\\/g, "/").replace(/^\.\//, "");
  const isAbsolute = normalized.startsWith("/");
  const encoded = normalized.split("/").filter(Boolean).map((s) => encodeURIComponent(s)).join("/");
  return `/emulation/media/${isAbsolute ? "/" : ""}${encoded}`;
}

// ─── GeoChip ─────────────────────────────────────────────────────────────────

function GeoChip({ code, showName = false, size = "sm" }: { code: string | null; showName?: boolean; size?: "sm" | "lg" }) {
  if (!code) return null;
  const c = countryOf(code);
  if (size === "lg") {
    return (
      <span
        className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-full text-[12.5px] font-medium"
        style={{ background: "var(--panel-soft)", boxShadow: "inset 0 0 0 1px var(--line)", color: "var(--ink-secondary)" }}
      >
        <span className="text-[14px] leading-none">{c.flag}</span>
        {showName ? c.name : code.toUpperCase()}
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 h-6 px-1.5 rounded-md text-[11px] font-semibold tabular-nums"
      style={{ background: "rgba(255,255,255,0.92)", color: "var(--ink)", boxShadow: "0 1px 2px rgba(0,0,0,0.08), inset 0 0 0 1px rgba(0,0,0,0.06)" }}
    >
      <span className="text-[12px] leading-none">{c.flag}</span>
      {code.toUpperCase()}
    </span>
  );
}

// ─── ResultPill ───────────────────────────────────────────────────────────────

function ResultPill({ ad, size = "sm" }: { ad: AdEntry; size?: "sm" | "md" }) {
  const result = getAnalysisResult(ad);
  const isPending = !result && (ad.analysis_status === "pending" || ad.analysis_status === "queued");
  const dim = size === "sm" ? "h-6 px-2 text-[11.5px]" : "h-7 px-2.5 text-[12.5px]";

  if (result === "relevant")
    return (
      <span className={`inline-flex items-center gap-1 rounded-full font-medium ${dim}`} style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>
        <Check size={11} strokeWidth={2.5} /> Релевантно
      </span>
    );
  if (result === "not_relevant")
    return (
      <span className={`inline-flex items-center gap-1 rounded-full font-medium ${dim}`} style={{ background: "var(--bg-soft)", color: "var(--ink-secondary)" }}>
        <X size={11} strokeWidth={2.5} /> Не релевантно
      </span>
    );
  if (isPending)
    return (
      <span className={`inline-flex items-center gap-1 rounded-full font-medium ${dim}`} style={{ background: "rgba(9,132,227,0.08)", color: "#0984e3" }}>
        <Sparkles size={11} strokeWidth={2} /> Анализ…
      </span>
    );
  return (
    <span className={`inline-flex items-center gap-1 rounded-full font-medium ${dim}`} style={{ background: "var(--panel-soft)", color: "var(--muted)" }}>
      Нет анализа
    </span>
  );
}

// ─── MetricsStrip ─────────────────────────────────────────────────────────────

interface Stats {
  total: number;
  relevant: number;
  notRelevant: number;
  pending: number;
  sessions: number;
  countries: number;
}

function MetricsStrip({ stats }: { stats: Stats }) {
  const pct = Math.round((stats.relevant / Math.max(1, stats.total)) * 100);
  return (
    <div
      className="rounded-2xl flex items-stretch flex-wrap"
      style={{ background: "var(--panel)", boxShadow: "inset 0 0 0 1px var(--line)" }}
    >
      <div className="flex-1 min-w-[140px] px-4 py-3 border-r" style={{ borderColor: "var(--line)" }}>
        <div className="text-[11px] uppercase tracking-wider font-semibold" style={{ color: "var(--muted)" }}>Всего рекламы</div>
        <div className="mt-0.5 text-[22px] font-semibold tabular-nums leading-none" style={{ color: "var(--ink)" }}>{stats.total}</div>
        <div className="mt-1 text-[12px]" style={{ color: "var(--muted)" }}>{stats.sessions} сессий · {stats.countries} стран</div>
      </div>
      <div className="flex-1 min-w-[140px] px-4 py-3 border-r" style={{ borderColor: "var(--line)" }}>
        <div className="text-[11px] uppercase tracking-wider font-semibold" style={{ color: "var(--muted)" }}>Релевантные</div>
        <div className="mt-0.5 flex items-baseline gap-2">
          <div className="text-[22px] font-semibold tabular-nums leading-none" style={{ color: "var(--accent)" }}>{stats.relevant}</div>
          <div className="text-[12px] tabular-nums" style={{ color: "var(--muted)" }}>{pct}%</div>
        </div>
        <div className="mt-2 h-1 rounded-full overflow-hidden" style={{ background: "var(--bg-soft)" }}>
          <div className="h-full rounded-full" style={{ width: `${pct}%`, background: "var(--accent)" }} />
        </div>
      </div>
      <div className="flex-1 min-w-[140px] px-4 py-3 border-r" style={{ borderColor: "var(--line)" }}>
        <div className="text-[11px] uppercase tracking-wider font-semibold" style={{ color: "var(--muted)" }}>Не релевантные</div>
        <div className="mt-0.5 text-[22px] font-semibold tabular-nums leading-none" style={{ color: "var(--danger)" }}>{stats.notRelevant}</div>
        <div className="mt-1 text-[12px]" style={{ color: "var(--muted)" }}>отфильтровано анализом</div>
      </div>
      <div className="flex-1 min-w-[140px] px-4 py-3">
        <div className="text-[11px] uppercase tracking-wider font-semibold" style={{ color: "var(--muted)" }}>В обработке</div>
        <div className="mt-0.5 flex items-baseline gap-2">
          <div className="text-[22px] font-semibold tabular-nums leading-none" style={{ color: "#0984e3" }}>{stats.pending}</div>
          {stats.pending > 0 && (
            <div className="flex gap-0.5 mb-0.5">
              {[0, 1, 2].map((i) => (
                <span key={i} className="w-1.5 h-1.5 rounded-full" style={{ background: "#0984e3", animation: `blink 1.4s ${i * 0.2}s infinite` }} />
              ))}
            </div>
          )}
        </div>
        <div className="mt-1 text-[12px]" style={{ color: "var(--muted)" }}>очередь анализа</div>
      </div>
    </div>
  );
}

// ─── GeoPopover ───────────────────────────────────────────────────────────────

function GeoPopover({
  geo,
  setGeo,
  geoCounts,
}: {
  geo: string[];
  setGeo: (v: string[]) => void;
  geoCounts: Record<string, number>;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onEsc);
    return () => { document.removeEventListener("mousedown", onDocClick); document.removeEventListener("keydown", onEsc); };
  }, [open]);

  const entries = Object.entries(geoCounts).sort((a, b) => b[1] - a[1]);
  const totalCountries = entries.length;
  const filtered = entries.filter(([code]) => {
    if (!q.trim()) return true;
    const c = countryOf(code);
    return `${code} ${c.name}`.toLowerCase().includes(q.trim().toLowerCase());
  });

  const toggle = (code: string) => setGeo(geo.includes(code) ? geo.filter((c) => c !== code) : [...geo, code]);
  const allSelected = geo.length === 0;
  const selectedCount = geo.length;

  const triggerLabel = () => {
    if (allSelected) return (
      <>
        <span style={{ color: "var(--ink-secondary)" }}>Все страны</span>
        <span className="text-[11px] tabular-nums" style={{ color: "var(--muted)" }}>{totalCountries}</span>
      </>
    );
    if (selectedCount === 1) {
      const code = geo[0];
      const c = countryOf(code);
      return (
        <>
          <span className="text-[15px] leading-none">{c.flag}</span>
          <span className="truncate max-w-[140px]">{c.name}</span>
          <span className="text-[11px] tabular-nums" style={{ color: "var(--muted)" }}>{geoCounts[code] || 0}</span>
        </>
      );
    }
    return (
      <>
        <span className="flex items-center -space-x-1">
          {geo.slice(0, 3).map((code) => (
            <span key={code} className="inline-grid place-items-center w-5 h-5 rounded-full text-[12px] leading-none ring-2" style={{ background: "var(--panel-soft)", boxShadow: "0 0 0 2px var(--panel)" }}>
              {countryOf(code).flag}
            </span>
          ))}
        </span>
        <span style={{ color: "var(--ink)" }}>{selectedCount} стран</span>
      </>
    );
  };

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="h-10 px-3 inline-flex items-center gap-2 rounded-xl text-[13px] font-medium transition-colors"
        style={{
          background: open ? "var(--panel-soft)" : "var(--panel)",
          boxShadow: "inset 0 0 0 1px var(--line)",
          color: "var(--ink)",
        }}
      >
        <Globe size={15} style={{ color: "var(--muted)" }} />
        {triggerLabel()}
        {!allSelected && (
          <span
            role="button"
            tabIndex={0}
            onClick={(e) => { e.stopPropagation(); setGeo([]); }}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.stopPropagation(); setGeo([]); } }}
            className="ml-0.5 h-5 w-5 grid place-items-center rounded-md cursor-pointer"
            style={{ color: "var(--muted)" }}
          >
            <X size={12} strokeWidth={2.2} />
          </span>
        )}
        <ChevronDown size={14} style={{ color: "var(--muted)", transform: open ? "rotate(180deg)" : "none", transition: "transform 0.15s" }} />
      </button>

      {open && (
        <div
          className="absolute right-0 top-[calc(100%+6px)] z-30 w-[340px] rounded-xl overflow-hidden"
          style={{ background: "var(--panel)", boxShadow: "0 8px 24px rgba(0,0,0,0.10), 0 2px 6px rgba(0,0,0,0.04), inset 0 0 0 1px var(--line-strong)" }}
        >
          <div className="p-2 flex items-center gap-2 border-b" style={{ borderColor: "var(--line)" }}>
            <div className="relative flex-1">
              <span className="absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: "var(--muted)" }}>
                <Search size={14} />
              </span>
              <input
                autoFocus
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Поиск страны…"
                className="w-full h-8 pl-8 pr-2 rounded-lg text-[12.5px] outline-none"
                style={{ background: "var(--panel-soft)", boxShadow: "inset 0 0 0 1px var(--line)", color: "var(--ink)" }}
              />
            </div>
            {selectedCount > 0 && (
              <button onClick={() => setGeo([])} className="h-8 px-2.5 rounded-lg text-[12px] font-medium hover:opacity-80" style={{ color: "var(--ink-secondary)" }}>
                Сбросить
              </button>
            )}
          </div>

          <div className="px-3 pt-2 pb-1 flex items-center justify-between">
            <span className="text-[11px] uppercase tracking-wider font-semibold" style={{ color: "var(--muted)" }}>
              {selectedCount > 0 ? `Выбрано · ${selectedCount}` : "Все страны"}
            </span>
            {filtered.length > 0 && q.trim() && (
              <button
                onClick={() => { const codes = filtered.map(([c]) => c); setGeo(Array.from(new Set([...geo, ...codes]))); }}
                className="text-[11.5px] font-medium hover:underline"
                style={{ color: "var(--brand)" }}
              >
                Выбрать всё
              </button>
            )}
          </div>

          <div className="max-h-[280px] overflow-y-auto pb-1">
            {filtered.length === 0 && <div className="px-3 py-6 text-center text-[12.5px]" style={{ color: "var(--muted)" }}>Ничего не найдено</div>}
            {filtered.map(([code, n]) => {
              const c = countryOf(code);
              const sel = geo.includes(code);
              return (
                <button
                  key={code}
                  onClick={() => toggle(code)}
                  className="w-full flex items-center gap-2.5 h-9 px-3 text-[13px] text-left transition-colors"
                  style={{ background: sel ? "var(--panel-soft)" : undefined }}
                >
                  <span
                    className="shrink-0 w-4 h-4 rounded grid place-items-center transition-colors"
                    style={{ background: sel ? "var(--brand)" : "transparent", boxShadow: sel ? "none" : "inset 0 0 0 1.5px var(--line-strong)" }}
                  >
                    {sel && <Check size={10} strokeWidth={3} className="text-white" />}
                  </span>
                  <span className="w-5 text-[16px] leading-none">{c.flag}</span>
                  <span className="flex-1 truncate" style={{ color: "var(--ink)" }}>{c.name}</span>
                  <span className="text-[11px] font-mono tracking-wider" style={{ color: "var(--muted)" }}>{code}</span>
                  <span className="ml-2 text-[11.5px] tabular-nums min-w-[20px] text-right" style={{ color: "var(--muted)" }}>{n}</span>
                </button>
              );
            })}
          </div>

          <div className="px-2 py-2 border-t flex items-center justify-between gap-2" style={{ borderColor: "var(--line)" }}>
            <span className="text-[11.5px] px-1" style={{ color: "var(--muted)" }}>
              {selectedCount === 0 ? "Без фильтра по гео" : `${selectedCount} из ${totalCountries}`}
            </span>
            <button onClick={() => setOpen(false)} className="h-8 px-3 rounded-lg text-[12.5px] font-semibold text-white" style={{ background: "var(--brand)" }}>
              Готово
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Toolbar ──────────────────────────────────────────────────────────────────

interface ToolbarProps {
  search: string;
  setSearch: (v: string) => void;
  analysis: AnalysisFilter;
  setAnalysis: (v: AnalysisFilter) => void;
  geo: string[];
  setGeo: (v: string[]) => void;
  geoCounts: Record<string, number>;
  analysisCounts: Record<string, number>;
  onRefresh: () => void;
}

function Pill({ active, onClick, children, count }: { active: boolean; onClick: () => void; children: React.ReactNode; count?: number }) {
  return (
    <button
      onClick={onClick}
      className="inline-flex items-center gap-1.5 h-8 px-3 rounded-full text-[13px] font-medium transition-colors whitespace-nowrap"
      style={
        active
          ? { background: "var(--ink)", color: "#fff" }
          : { background: "var(--panel)", boxShadow: "inset 0 0 0 1px var(--line)", color: "var(--ink-secondary)" }
      }
    >
      {children}
      {count != null && (
        <span className="text-[11px] tabular-nums" style={{ opacity: active ? 0.7 : 1, color: active ? undefined : "var(--muted)" }}>{count}</span>
      )}
    </button>
  );
}

function Toolbar({ search, setSearch, analysis, setAnalysis, geo, setGeo, geoCounts, analysisCounts, onRefresh }: ToolbarProps) {
  return (
    <div className="sticky top-0 z-20 -mx-1 px-1 pt-1 pb-3" style={{ background: "linear-gradient(var(--bg) 70%, rgba(248,249,251,0))" }}>
      <div className="rounded-2xl p-2.5 flex flex-col gap-2.5" style={{ background: "var(--panel)", boxShadow: "inset 0 0 0 1px var(--line)" }}>
        {/* Row 1: search + geo + refresh */}
        <div className="flex items-center gap-2">
          <div className="flex-1 relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--muted)" }}>
              <Search size={16} />
            </span>
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Поиск по рекламодателю, заголовку, домену…"
              className="w-full h-10 pl-9 pr-9 rounded-xl text-[13.5px] outline-none"
              style={{ background: "var(--panel-soft)", boxShadow: "inset 0 0 0 1px var(--line)", color: "var(--ink)" }}
            />
            {search && (
              <button onClick={() => setSearch("")} className="absolute right-2 top-1/2 -translate-y-1/2 h-6 w-6 grid place-items-center rounded-md" style={{ color: "var(--muted)" }}>
                <X size={14} />
              </button>
            )}
          </div>

          <GeoPopover geo={geo} setGeo={setGeo} geoCounts={geoCounts} />

          <button
            onClick={onRefresh}
            className="hidden md:inline-flex h-10 px-3.5 items-center gap-1.5 rounded-xl text-[13px] font-semibold text-white transition-opacity hover:opacity-90"
            style={{ background: "var(--brand)" }}
          >
            <RefreshCw size={15} /> Обновить
          </button>
        </div>

        {/* Row 2: analysis pills */}
        <div className="flex items-center gap-2 overflow-x-auto" style={{ scrollbarWidth: "none" }}>
          <span className="shrink-0 text-[11px] uppercase tracking-wider font-semibold pl-1 pr-1" style={{ color: "var(--muted)" }}>Анализ</span>
          <Pill active={analysis === "all"} onClick={() => setAnalysis("all")} count={analysisCounts.all}>Все</Pill>
          <Pill active={analysis === "relevant"} onClick={() => setAnalysis("relevant")} count={analysisCounts.relevant}>
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: "var(--accent)" }} /> Релевантные
          </Pill>
          <Pill active={analysis === "not_relevant"} onClick={() => setAnalysis("not_relevant")} count={analysisCounts.not_relevant}>
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: "var(--danger)" }} /> Не релевантные
          </Pill>
          <Pill active={analysis === "pending"} onClick={() => setAnalysis("pending")} count={analysisCounts.pending}>
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: "#0984e3" }} /> Без анализа
          </Pill>
        </div>

        {/* Row 3: selected geo chips (only shows when 2+ countries selected) */}
        {geo.length >= 2 && (
          <div className="flex items-center gap-1.5 flex-wrap pl-1">
            <span className="text-[11px] uppercase tracking-wider font-semibold pr-1" style={{ color: "var(--muted)" }}>Гео</span>
            {geo.map((code) => {
              const c = countryOf(code);
              return (
                <span
                  key={code}
                  className="inline-flex items-center gap-1 h-7 pl-2 pr-1 rounded-full text-[12px] font-medium"
                  style={{ background: "var(--brand-soft)", color: "var(--brand-strong)" }}
                >
                  <span className="text-[13px] leading-none">{c.flag}</span>
                  {c.name}
                  <span className="tabular-nums opacity-70 text-[11px]">{geoCounts[code] || 0}</span>
                  <button onClick={() => setGeo(geo.filter((x) => x !== code))} className="ml-0.5 h-5 w-5 grid place-items-center rounded-full hover:bg-white/60">
                    <X size={11} strokeWidth={2.4} />
                  </button>
                </span>
              );
            })}
            <button onClick={() => setGeo([])} className="h-7 px-2.5 rounded-full text-[12px] font-medium hover:opacity-80" style={{ color: "var(--ink-secondary)" }}>
              Сбросить все
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── RelevanceMeter ──────────────────────────────────────────────────────────

function RelevanceMeter({ relevant, total }: { relevant: number; total: number }) {
  const pct = total > 0 ? Math.round((relevant / total) * 100) : 0;
  const cells = Math.min(total, 16);
  const filled = Math.round((relevant / Math.max(1, total)) * cells);
  const isZero = relevant === 0;

  return (
    <span
      className="inline-flex items-center gap-2 h-8 pl-2.5 pr-3 rounded-full"
      style={{ boxShadow: "inset 0 0 0 1px var(--line)", background: "var(--panel-soft)" }}
      title={`${relevant} релевантных из ${total} · ${pct}%`}
    >
      <span className="flex items-center gap-[2px]">
        {Array.from({ length: cells }).map((_, i) => (
          <span
            key={i}
            className="block w-[3px] h-3 rounded-sm"
            style={{ background: i < filled ? "var(--accent)" : "var(--line-strong)" }}
          />
        ))}
      </span>
      <span className="flex items-baseline gap-1 text-[12.5px] tabular-nums leading-none">
        <span className="font-semibold" style={{ color: isZero ? "var(--ink-secondary)" : "var(--accent)" }}>{relevant}</span>
        <span style={{ color: "var(--muted)" }}>/</span>
        <span className="font-medium" style={{ color: "var(--ink-secondary)" }}>{total}</span>
      </span>
    </span>
  );
}

// ─── Ad Thumbnail ─────────────────────────────────────────────────────────────

function AdThumbnail({ ad }: { ad: AdEntry }) {
  const firstShot = getPreferredAdScreenshot(ad.screenshot_paths);
  const shotUrl = firstShot ? buildMediaPath(firstShot.file_path) : null;
  const hasVideo = Boolean(ad.video_file && ad.video_status === "completed");
  const [failed, setFailed] = useState(false);

  if (shotUrl && !failed) {
    return (
      <div className="relative w-full overflow-hidden rounded-xl" style={{ background: "var(--panel-soft)" }}>
        <img
          src={shotUrl}
          alt=""
          className="w-full object-contain"
          style={{ aspectRatio: "16/10" }}
          loading="lazy"
          onError={() => setFailed(true)}
        />
        {hasVideo && (
          <div className="absolute left-2 bottom-2 flex items-center gap-1 rounded-md px-1.5 py-1 text-[10px] font-semibold text-white" style={{ background: "rgba(0,0,0,0.55)" }}>
            <Film size={9} /> Видео
          </div>
        )}
        {ad.session_proxy_country && (
          <div className="absolute top-1.5 right-1.5">
            <GeoChip code={ad.session_proxy_country} size="sm" />
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="flex w-full items-center justify-center rounded-xl" style={{ aspectRatio: "16/10", background: "var(--panel-soft)" }}>
      {hasVideo ? <Film size={24} style={{ color: "var(--muted)" }} /> : <Image size={24} style={{ color: "var(--muted)" }} />}
    </div>
  );
}

// ─── Ad Card ─────────────────────────────────────────────────────────────────

function AdCard({ ad, onClick }: { ad: AdEntry; onClick: () => void }) {
  const { name: advertiser, domain } = resolveAdIdentity(ad);
  const category = ad.analysis_summary?.["category"] as string | undefined;

  return (
    <article
      onClick={onClick}
      className="w-[260px] shrink-0 flex flex-col gap-2.5 p-2.5 rounded-2xl cursor-pointer transition-shadow hover:shadow-md"
      style={{ background: "var(--panel)", boxShadow: "inset 0 0 0 1px var(--line)", width: 260, minWidth: 260 }}
    >
      <AdThumbnail ad={ad} />

      <div className="px-1 flex flex-col gap-0.5">
        <div className="flex items-center justify-between gap-2">
          <div className="text-[13.5px] font-semibold truncate" style={{ color: "var(--ink)" }}>{advertiser}</div>
          <ExternalLink size={13} style={{ color: "var(--muted)", flexShrink: 0 }} />
        </div>
        {domain && domain !== advertiser && (
          <div className="text-[11.5px] truncate" style={{ color: "var(--muted)" }}>{domain}</div>
        )}
      </div>

      {ad.headline_text && !/^(subscribe to|подпишитесь на)/i.test(ad.headline_text) && (
        <p className="px-1 text-[12.5px] leading-snug line-clamp-2" style={{ color: "var(--ink-secondary)", minHeight: 34 }}>{ad.headline_text}</p>
      )}

      <div className="px-1 flex items-center justify-between gap-2 mt-auto">
        <ResultPill ad={ad} />
        <div className="flex items-center gap-1.5 text-[11.5px]" style={{ color: "var(--muted)" }}>
          {ad.ad_duration_seconds != null && (
            <span className="inline-flex items-center gap-1 tabular-nums">
              <Clock size={11} /> {ad.ad_duration_seconds.toFixed(0)}с
            </span>
          )}
          {category && (
            <>
              <span className="w-0.5 h-0.5 rounded-full bg-current opacity-40" />
              <span className="rounded-md px-2 py-0.5 text-[11px]" style={{ background: "var(--panel-soft)", color: "var(--muted)" }}>{category}</span>
            </>
          )}
        </div>
      </div>
    </article>
  );
}

// ─── Session Row ──────────────────────────────────────────────────────────────

function SessionRow({ group, onAdClick }: { group: SessionGroup; onAdClick: (ad: AdEntry) => void }) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ left: false, right: true });

  const updateEdges = useCallback(() => {
    const el = scrollerRef.current;
    if (!el) return;
    setEdges({ left: el.scrollLeft > 4, right: el.scrollLeft + el.clientWidth < el.scrollWidth - 4 });
  }, []);

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    updateEdges();
    el.addEventListener("scroll", updateEdges, { passive: true });
    const ro = new ResizeObserver(updateEdges);
    ro.observe(el);
    return () => { el.removeEventListener("scroll", updateEdges); ro.disconnect(); };
  }, [updateEdges]);

  const scrollBy = (dir: number) => {
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollBy({ left: dir * (el.clientWidth - 64), behavior: "smooth" });
  };

  const relevant = group.ads.filter((a) => getAnalysisResult(a) === "relevant").length;

  return (
    <section className="rounded-2xl" style={{ background: "var(--panel)", boxShadow: "inset 0 0 0 1px var(--line)" }}>
      <header className="px-4 pt-3.5 pb-3 flex items-start gap-3 flex-wrap">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <GeoChip code={group.session_proxy_country} showName size="lg" />
            <span className="text-[13px] font-medium" style={{ color: "var(--ink-secondary)" }}>{formatDate(group.session_started_at ?? "")}</span>
            <span style={{ color: "var(--muted)" }}>·</span>
            <code className="text-[11.5px] font-mono" style={{ color: "var(--muted)" }}>{group.session_id.slice(0, 8)}</code>
          </div>
          {group.session_topics.length > 0 && (
            <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
              {group.session_topics.map((t) => (
                <span
                  key={t}
                  className="inline-flex items-center h-6 px-2 rounded-md text-[11.5px] font-medium"
                  style={{ background: "var(--brand-soft)", color: "var(--brand-strong)" }}
                >
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <RelevanceMeter relevant={relevant} total={group.ads.length} />
          <Link
            to={`/sessions/${group.session_id}`}
            className="inline-flex items-center gap-1 h-7 px-2.5 rounded-full text-[12px] font-medium hover:opacity-80"
            style={{ color: "var(--ink-secondary)" }}
          >
            Сессия <ExternalLink size={11} />
          </Link>
          <div className="flex items-center gap-1 ml-1">
            <button
              onClick={() => scrollBy(-1)}
              disabled={!edges.left}
              className="h-8 w-8 grid place-items-center rounded-lg disabled:opacity-40 disabled:cursor-not-allowed transition-opacity"
              style={{ boxShadow: "inset 0 0 0 1px var(--line)", background: "var(--panel-soft)", color: "var(--ink-secondary)" }}
            >
              <ChevronLeft size={15} />
            </button>
            <button
              onClick={() => scrollBy(1)}
              disabled={!edges.right}
              className="h-8 w-8 grid place-items-center rounded-lg disabled:opacity-40 disabled:cursor-not-allowed transition-opacity"
              style={{ boxShadow: "inset 0 0 0 1px var(--line)", background: "var(--panel-soft)", color: "var(--ink-secondary)" }}
            >
              <ChevronRight size={15} />
            </button>
          </div>
        </div>
      </header>

      <div className="relative">
        <div
          className="pointer-events-none absolute left-0 top-0 bottom-3 w-8 z-10 transition-opacity"
          style={{ opacity: edges.left ? 1 : 0, background: "linear-gradient(to right, var(--panel), rgba(255,255,255,0))" }}
        />
        <div
          className="pointer-events-none absolute right-0 top-0 bottom-3 w-8 z-10 transition-opacity"
          style={{ opacity: edges.right ? 1 : 0, background: "linear-gradient(to left, var(--panel), rgba(255,255,255,0))" }}
        />
        <div
          ref={scrollerRef}
          className="flex gap-3 overflow-x-auto px-4 pb-4 pt-1"
          style={{ scrollbarWidth: "thin" }}
        >
          {group.ads.map((ad) => (
            <AdCard
              key={`${ad.session_id}-${ad.ad_position}-${ad._index}`}
              ad={ad}
              onClick={() => onAdClick(ad)}
            />
          ))}
        </div>
      </div>
    </section>
  );
}

// ─── Pagination ───────────────────────────────────────────────────────────────

function Pagination({ page, totalPages, totalItems, pageSize, onChange }: {
  page: number; totalPages: number; totalItems: number; pageSize: number; onChange: (p: number) => void;
}) {
  if (totalPages <= 1) {
    return (
      <div className="pt-2 pb-2 text-center text-[12.5px]" style={{ color: "var(--muted)" }}>
        Показаны все {totalItems} сессий
      </div>
    );
  }

  const pages: (number | "…")[] = [];
  for (let i = 1; i <= totalPages; i++) {
    if (i === 1 || i === totalPages || (i >= page - 1 && i <= page + 1)) pages.push(i);
    else if (pages[pages.length - 1] !== "…") pages.push("…");
  }

  const from = (page - 1) * pageSize + 1;
  const to = Math.min(totalItems, page * pageSize);

  return (
    <div className="rounded-2xl px-3 py-2 flex items-center justify-between gap-3 flex-wrap" style={{ background: "var(--panel)", boxShadow: "inset 0 0 0 1px var(--line)" }}>
      <div className="text-[12.5px] px-1" style={{ color: "var(--muted)" }}>
        <span className="font-semibold tabular-nums" style={{ color: "var(--ink-secondary)" }}>{from}–{to}</span>
        {" из "}
        <span className="font-semibold tabular-nums" style={{ color: "var(--ink-secondary)" }}>{totalItems}</span>
        {" сессий"}
      </div>
      <div className="flex items-center gap-0.5">
        <button
          onClick={() => onChange(page - 1)}
          disabled={page <= 1}
          className="min-w-[34px] h-9 px-2 rounded-lg text-[13px] font-medium inline-flex items-center justify-center disabled:opacity-40"
          style={{ color: "var(--ink-secondary)" }}
        >
          <ChevronLeft size={15} />
        </button>
        {pages.map((p, i) =>
          p === "…"
            ? <span key={"e" + i} className="px-1 text-[13px]" style={{ color: "var(--muted)" }}>…</span>
            : (
              <button
                key={p}
                onClick={() => onChange(p as number)}
                className="min-w-[34px] h-9 px-2 rounded-lg text-[13px] font-medium tabular-nums inline-flex items-center justify-center transition-colors"
                style={p === page ? { background: "var(--ink)", color: "#fff" } : { color: "var(--ink-secondary)" }}
              >
                {p}
              </button>
            )
        )}
        <button
          onClick={() => onChange(page + 1)}
          disabled={page >= totalPages}
          className="min-w-[34px] h-9 px-2 rounded-lg text-[13px] font-medium inline-flex items-center justify-center disabled:opacity-40"
          style={{ color: "var(--ink-secondary)" }}
        >
          <ChevronRight size={15} />
        </button>
      </div>
    </div>
  );
}

// ─── Ad Modal ─────────────────────────────────────────────────────────────────

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
        <div className="flex items-center justify-between border-b px-5 py-4" style={{ borderColor: "var(--line)" }}>
          <div className="min-w-0 flex items-center gap-2">
            <GeoChip code={ad.session_proxy_country} size="sm" />
            <div className="min-w-0">
              <div className="truncate font-semibold" style={{ color: "var(--ink)" }}>{advertiser}</div>
              {domain && domain !== advertiser && <div className="truncate text-xs" style={{ color: "var(--muted)" }}>{domain}</div>}
            </div>
          </div>
          <button
            onClick={onClose}
            className="ml-3 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl transition"
            style={{ color: "var(--muted)" }}
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto">
          <div className="space-y-4 p-5">
            {result && (
              <div className={`rounded-xl px-4 py-3 ${result === "relevant" ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-800"}`}>
                <div className="flex items-center gap-2 font-semibold text-sm">
                  {result === "relevant" ? <><Check size={15} /> Релевантная</> : <><X size={15} /> Не релевантная</>}
                  {category && <span className="font-normal opacity-60">· {category}</span>}
                </div>
                {reason && <p className="mt-1 text-xs opacity-75">{reason}</p>}
              </div>
            )}

            {hasVideo && ad.video_file && (
              <video
                src={buildMediaPath(ad.video_file) ?? undefined}
                controls
                className="max-h-[70vh] w-full rounded-xl bg-black object-contain"
              />
            )}

            {ad.screenshot_paths.length > 0 && (
              <div>
                <div className="mb-2 text-xs font-semibold uppercase tracking-wide" style={{ color: "var(--muted)" }}>Скриншоты</div>
                <div className="flex gap-2 overflow-x-auto pb-1">
                  {ad.screenshot_paths.map((p) => {
                    const url = buildMediaPath(p.file_path);
                    return url ? (
                      <a key={p.offset_ms} href={url} target="_blank" rel="noreferrer" className="shrink-0">
                        <img src={url} alt="" className="h-32 w-56 rounded-lg border bg-[var(--panel-soft)] object-contain transition hover:opacity-80" style={{ borderColor: "var(--line)" }} />
                      </a>
                    ) : null;
                  })}
                </div>
              </div>
            )}

            {ad.headline_text && (
              <div>
                <div className="mb-1 text-xs font-semibold uppercase tracking-wide" style={{ color: "var(--muted)" }}>Заголовок</div>
                <p className="text-sm" style={{ color: "var(--ink)" }}>{ad.headline_text}</p>
              </div>
            )}

            <div className="grid grid-cols-2 gap-3">
              {ad.ad_duration_seconds != null && (
                <div className="rounded-xl px-4 py-3" style={{ background: "var(--panel-soft)" }}>
                  <div className="text-[11px]" style={{ color: "var(--muted)" }}>Длительность</div>
                  <div className="font-semibold" style={{ color: "var(--ink)" }}>{ad.ad_duration_seconds.toFixed(0)}с</div>
                </div>
              )}
              <div className="rounded-xl px-4 py-3" style={{ background: "var(--panel-soft)" }}>
                <div className="text-[11px]" style={{ color: "var(--muted)" }}>Дата</div>
                <div className="font-semibold" style={{ color: "var(--ink)" }}>{formatDate(ad.session_started_at ?? "")}</div>
              </div>
            </div>

            {ad.session_topics.length > 0 && (
              <div>
                <div className="mb-2 text-xs font-semibold uppercase tracking-wide" style={{ color: "var(--muted)" }}>Темы сессии</div>
                <div className="flex flex-wrap gap-1.5">
                  {ad.session_topics.map((t) => (
                    <span key={t} className="rounded-lg px-2.5 py-1 text-xs" style={{ background: "var(--panel-soft)", color: "var(--ink-secondary)" }}>{t}</span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 border-t px-5 py-3" style={{ borderColor: "var(--line)" }}>
          {ad.landing_url && (
            <a href={ad.landing_url} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs transition hover:opacity-80" style={{ borderColor: "var(--line)", color: "var(--ink-secondary)" }}>
              <ExternalLink size={12} /> Лендинг
            </a>
          )}
          {ad.cta_href && ad.cta_href !== ad.landing_url && (
            <a href={ad.cta_href} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs transition hover:opacity-80" style={{ borderColor: "var(--line)", color: "var(--ink-secondary)" }}>
              <ExternalLink size={12} /> CTA
            </a>
          )}
          <Link
            to={`/sessions/${ad.session_id}`}
            onClick={onClose}
            className="ml-auto flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-white transition hover:opacity-90"
            style={{ background: "var(--brand)" }}
          >
            <ExternalLink size={12} /> Открыть сессию
          </Link>
        </div>
      </div>
    </div>
  );
}

// ─── Main screen ──────────────────────────────────────────────────────────────

const PAGE_SIZE = 5;

export function AdsScreen() {
  const [items, setItems] = useState<EmulationHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedAd, setSelectedAd] = useState<AdEntry | null>(null);

  const [search, setSearch] = useState("");
  const [analysisFilter, setAnalysisFilter] = useState<AnalysisFilter>("relevant");
  const [geo, setGeo] = useState<string[]>([]);
  const [page, setPage] = useState(1);

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
          session_proxy_country: item.proxy_country_code ?? null,
          _index: result.length,
        });
      }
    }
    return result;
  }, [items]);

  // counts for toolbar (over full dataset)
  const analysisCounts = useMemo(() => {
    const c = { all: allAds.length, relevant: 0, not_relevant: 0, pending: 0 };
    for (const ad of allAds) {
      const k = getResultKey(ad);
      c[k]++;
    }
    return c;
  }, [allAds]);

  const geoCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const ad of allAds) {
      if (ad.session_proxy_country) c[ad.session_proxy_country] = (c[ad.session_proxy_country] || 0) + 1;
    }
    return c;
  }, [allAds]);

  const stats: Stats = useMemo(() => {
    const total = allAds.length;
    const relevant = allAds.filter((a) => getAnalysisResult(a) === "relevant").length;
    const notRelevant = allAds.filter((a) => getAnalysisResult(a) === "not_relevant").length;
    const pending = allAds.filter((a) => getAnalysisResult(a) === null).length;
    const sessions = new Set(allAds.map((a) => a.session_id)).size;
    const countries = new Set(allAds.map((a) => a.session_proxy_country).filter(Boolean)).size;
    return { total, relevant, notRelevant, pending, sessions, countries };
  }, [allAds]);

  // filtered + grouped
  const filteredGroups = useMemo(() => {
    const q = search.trim().toLowerCase();

    const sessionMap = new Map<string, SessionGroup>();
    for (const ad of allAds) {
      if (geo.length > 0 && (!ad.session_proxy_country || !geo.includes(ad.session_proxy_country))) continue;
      if (analysisFilter !== "all" && getResultKey(ad) !== analysisFilter) continue;
      if (q) {
        const { name, domain } = resolveAdIdentity(ad);
        const hay = [name, domain, ad.headline_text, ...(ad.session_topics ?? [])].filter(Boolean).join(" ").toLowerCase();
        if (!hay.includes(q)) continue;
      }
      if (!sessionMap.has(ad.session_id)) {
        sessionMap.set(ad.session_id, {
          session_id: ad.session_id,
          session_started_at: ad.session_started_at,
          session_topics: ad.session_topics,
          session_proxy_country: ad.session_proxy_country,
          ads: [],
        });
      }
      sessionMap.get(ad.session_id)!.ads.push(ad);
    }

    const groups = Array.from(sessionMap.values());
    groups.sort((a, b) => (b.session_started_at ?? "").localeCompare(a.session_started_at ?? ""));
    return groups;
  }, [allAds, search, analysisFilter, geo]);

  useEffect(() => { setPage(1); }, [search, analysisFilter, geo]);

  const totalPages = Math.max(1, Math.ceil(filteredGroups.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const pagedGroups = filteredGroups.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);
  const visibleAdCount = filteredGroups.reduce((n, s) => n + s.ads.length, 0);

  if (loading) return <Loader label="Загрузка реклам…" />;
  if (error) return (
    <div className="flex h-64 flex-col items-center justify-center gap-4">
      <p className="text-sm" style={{ color: "var(--danger)" }}>{error}</p>
      <Button onClick={load}>Повторить</Button>
    </div>
  );

  return (
    <>
      {selectedAd && <AdModal ad={selectedAd} onClose={() => setSelectedAd(null)} />}

      <style>{`@keyframes blink { 0%, 60%, 100% { opacity: 0.25 } 30% { opacity: 1 } }`}</style>

      <div className="min-h-screen" style={{ background: "var(--bg)" }}>
        <div className="max-w-[1280px] mx-auto px-5 md:px-8 py-6 md:py-8 flex flex-col gap-5">
          {/* Page header */}
          <div className="flex items-end justify-between gap-4 flex-wrap">
            <div>
              <h1 className="text-[28px] md:text-[30px] font-semibold tracking-tight leading-none" style={{ color: "var(--ink)" }}>
                Реклама
              </h1>
              <p className="mt-2 text-[13.5px]" style={{ color: "var(--ink-secondary)" }}>
                Все рекламные объявления, захваченные в эмуляционных сессиях, сгруппированы по сессиям.
              </p>
            </div>
            <button
              onClick={load}
              className="md:hidden inline-flex h-10 px-3.5 items-center gap-1.5 rounded-xl text-[13px] font-semibold text-white"
              style={{ background: "var(--brand)" }}
            >
              <RefreshCw size={15} /> Обновить
            </button>
          </div>

          {/* Metrics */}
          <MetricsStrip stats={stats} />

          {/* Toolbar */}
          <Toolbar
            search={search} setSearch={setSearch}
            analysis={analysisFilter} setAnalysis={setAnalysisFilter}
            geo={geo} setGeo={setGeo}
            geoCounts={geoCounts}
            analysisCounts={analysisCounts}
            onRefresh={load}
          />

          {/* Result count row */}
          <div className="flex items-center justify-between -mt-2 px-1 text-[12.5px]" style={{ color: "var(--muted)" }}>
            <div>
              Показано{" "}
              <span className="font-semibold tabular-nums" style={{ color: "var(--ink-secondary)" }}>{visibleAdCount}</span> объявлений
              {" в "}
              <span className="font-semibold tabular-nums" style={{ color: "var(--ink-secondary)" }}>{filteredGroups.length}</span> сессиях
              {(geo.length > 0 || analysisFilter !== "all" || search) && (
                <button
                  onClick={() => { setGeo([]); setAnalysisFilter("all"); setSearch(""); }}
                  className="ml-3 inline-flex items-center gap-1 text-[12px] font-medium hover:opacity-80"
                  style={{ color: "var(--brand)" }}
                >
                  <X size={11} strokeWidth={2.2} /> Сбросить фильтры
                </button>
              )}
            </div>
          </div>

          {/* Session rows */}
          {filteredGroups.length === 0 ? (
            <EmptyState
              title="Ничего не найдено"
              description={allAds.length === 0 ? "Запустите эмуляцию — захваченные рекламы появятся здесь" : "Попробуйте изменить фильтры или поисковый запрос"}
              action={allAds.length > 0 ? <Button onClick={() => { setGeo([]); setAnalysisFilter("all"); setSearch(""); }}>Сбросить фильтры</Button> : undefined}
            />
          ) : (
            <>
              <div className="flex flex-col gap-4">
                {pagedGroups.map((group) => (
                  <SessionRow key={group.session_id} group={group} onAdClick={setSelectedAd} />
                ))}
              </div>
              <Pagination page={safePage} totalPages={totalPages} totalItems={filteredGroups.length} pageSize={PAGE_SIZE} onChange={setPage} />
            </>
          )}

          <div className="h-8" />
        </div>
      </div>
    </>
  );
}
