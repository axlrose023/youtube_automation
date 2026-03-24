import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { formatDate, formatMinutes, formatNumber } from "@/lib/format";
import { getStatusTone } from "@/lib/metrics";
import type { EmulationHistoryItem } from "@/types/api";

export function SessionTable({ items }: { items: EmulationHistoryItem[] }) {
  return (
    <Card className="overflow-hidden p-0">
      <div className="border-b border-[var(--line)] px-6 py-5">
        <div className="section-eyebrow">Audit trail</div>
        <div className="mt-1 text-lg font-semibold text-[var(--ink)]">Session list</div>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead className="bg-[var(--panel-soft)] text-left text-[var(--muted)]">
            <tr>
              <th className="px-6 py-4 font-medium">Session</th>
              <th className="px-6 py-4 font-medium">Status</th>
              <th className="px-6 py-4 font-medium">Requested</th>
              <th className="px-6 py-4 font-medium">Videos</th>
              <th className="px-6 py-4 font-medium">Ads</th>
              <th className="px-6 py-4 font-medium">Capture</th>
              <th className="px-6 py-4 font-medium">Queued</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.session_id} className="border-t border-[var(--line)] transition hover:bg-white/72">
                <td className="px-6 py-4">
                  <Link to={`/sessions/${item.session_id}`} className="font-semibold text-[var(--ink)] hover:text-[var(--brand)]">
                    {item.session_id.slice(0, 12)}
                  </Link>
                  <div className="mt-1 text-xs text-[var(--muted)]">
                    {item.requested_topics.slice(0, 2).join(", ")}
                  </div>
                </td>
                <td className="px-6 py-4">
                  <Badge tone={getStatusTone(item.status) as never}>{item.status}</Badge>
                </td>
                <td className="px-6 py-4 text-[var(--muted)]">
                  {item.requested_duration_minutes}m / {formatMinutes(item.elapsed_minutes)}
                </td>
                <td className="px-6 py-4 text-[var(--muted)]">{formatNumber(item.watched_videos_count)}</td>
                <td className="px-6 py-4 text-[var(--muted)]">{formatNumber(item.watched_ads_count)}</td>
                <td className="px-6 py-4 text-[var(--muted)]">
                  {item.captures.video_captures}/{item.captures.ads_total}
                </td>
                <td className="px-6 py-4 text-[var(--muted)]">{formatDate(item.queued_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
