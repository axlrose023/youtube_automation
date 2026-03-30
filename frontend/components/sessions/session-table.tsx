import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { formatDate, formatMinutes, formatNumber } from "@/lib/format";
import { formatSessionStatus, getStatusTone } from "@/lib/metrics";
import type { EmulationAdCapture, EmulationHistoryItem } from "@/types/api";

function resolveAdResult(capture: EmulationAdCapture): string | null {
  const value = capture.analysis_summary?.["result"];
  const result = typeof value === "string" && value.trim() ? value : null;
  if (result === "relevant" || result === "not_relevant") return result;
  if (capture.analysis_status === "completed") return "relevant";
  if (capture.analysis_status === "not_relevant") return "not_relevant";
  return null;
}

function countAdRelevance(captures?: EmulationAdCapture[] | null) {
  let relevant = 0;
  let notRelevant = 0;
  for (const c of captures ?? []) {
    const r = resolveAdResult(c);
    if (r === "relevant") relevant++;
    else if (r === "not_relevant") notRelevant++;
  }
  return { relevant, notRelevant };
}

function RelevantCell({ item }: { item: EmulationHistoryItem }) {
  const { relevant, notRelevant } = countAdRelevance(item.ad_captures);
  const total = relevant + notRelevant;
  if (total === 0) return <span className="text-[var(--muted)]">—</span>;
  return (
    <>
      <span className={relevant > 0 ? "text-emerald-500" : undefined}>{relevant}</span>
      <span className="text-[var(--muted)]">/{total}</span>
    </>
  );
}

/* ── Mobile card ── */

function SessionCard({ item }: { item: EmulationHistoryItem }) {
  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link
            to={`/sessions/${item.session_id}`}
            className="font-medium text-[var(--ink)] transition hover:text-[var(--brand)]"
          >
            {item.session_id.slice(0, 12)}
          </Link>
          <div className="mt-0.5 truncate text-xs text-[var(--muted)]">
            {item.requested_topics.join(", ") || "—"}
          </div>
        </div>
        <Badge tone={getStatusTone(item.status) as never}>{formatSessionStatus(item.status)}</Badge>
      </div>

      <div className="mt-3 grid grid-cols-3 gap-y-2 text-xs">
        <Stat label="Запрошено" value={`${item.requested_duration_minutes}m / ${formatMinutes(item.elapsed_minutes)}`} />
        <Stat label="Видео" value={formatNumber(item.videos_watched)} />
        <Stat label="Реклама" value={formatNumber(item.watched_ads_count)} />
        <Stat label="Релевантно">
          <RelevantCell item={item} />
        </Stat>
        <Stat label="Записано" value={`${item.captures.video_captures}/${item.captures.ads_total}`} />
        <Stat label="Очередь" value={formatDate(item.queued_at)} />
      </div>
    </Card>
  );
}

function Stat({ label, value, children }: { label: string; value?: string; children?: React.ReactNode }) {
  return (
    <div>
      <div className="text-[var(--muted)]">{label}</div>
      <div className="mt-0.5 text-sm text-[var(--ink)]">{children ?? value}</div>
    </div>
  );
}

/* ── Desktop table ── */

function DesktopTable({ items }: { items: EmulationHistoryItem[] }) {
  return (
    <Card className="overflow-hidden p-0">
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--line)] text-left text-xs text-[var(--muted)]">
              <th className="px-5 py-3 font-medium">Сессия</th>
              <th className="px-5 py-3 font-medium">Статус</th>
              <th className="px-5 py-3 font-medium">Запрошено</th>
              <th className="px-5 py-3 font-medium">Видео</th>
              <th className="px-5 py-3 font-medium">Реклама</th>
              <th className="px-5 py-3 font-medium">Релевантно</th>
              <th className="px-5 py-3 font-medium">Записано</th>
              <th className="px-5 py-3 font-medium">Очередь</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.session_id} className="border-t border-[var(--line)] transition-colors hover:bg-[var(--panel-hover)]">
                <td className="px-5 py-3">
                  <Link to={`/sessions/${item.session_id}`} className="font-medium text-[var(--ink)] transition hover:text-[var(--brand)]">
                    {item.session_id.slice(0, 12)}
                  </Link>
                  <div className="mt-0.5 text-xs text-[var(--muted)]">
                    {item.requested_topics.join(", ") || "—"}
                  </div>
                </td>
                <td className="px-5 py-3">
                  <Badge tone={getStatusTone(item.status) as never}>{formatSessionStatus(item.status)}</Badge>
                </td>
                <td className="px-5 py-3 text-[var(--muted)]">
                  {item.requested_duration_minutes}m / {formatMinutes(item.elapsed_minutes)}
                </td>
                <td className="px-5 py-3 text-[var(--muted)]">{formatNumber(item.videos_watched)}</td>
                <td className="px-5 py-3 text-[var(--muted)]">{formatNumber(item.watched_ads_count)}</td>
                <td className="px-5 py-3">
                  <RelevantCell item={item} />
                </td>
                <td className="px-5 py-3 text-[var(--muted)]">
                  {item.captures.video_captures}/{item.captures.ads_total}
                </td>
                <td className="px-5 py-3 text-[var(--muted)]">{formatDate(item.queued_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/* ── Export ── */

export function SessionTable({ items }: { items: EmulationHistoryItem[] }) {
  return (
    <>
      {/* Mobile: card stack */}
      <div className="flex flex-col gap-3 md:hidden">
        {items.map((item) => (
          <SessionCard key={item.session_id} item={item} />
        ))}
      </div>

      {/* Desktop: table */}
      <div className="hidden md:block">
        <DesktopTable items={items} />
      </div>
    </>
  );
}
