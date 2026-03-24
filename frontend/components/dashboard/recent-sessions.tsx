import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { formatDate, formatMinutes } from "@/lib/format";
import { getStatusTone } from "@/lib/metrics";
import type { EmulationHistoryItem } from "@/types/api";

export function RecentSessions({ items }: { items: EmulationHistoryItem[] }) {
  return (
    <Card className="overflow-hidden p-0">
      <div className="border-b border-[var(--line)] px-6 py-5">
        <div className="section-eyebrow">Recent activity</div>
        <div className="mt-1 flex items-center justify-between gap-4">
          <div className="text-lg font-semibold text-[var(--ink)]">Recent sessions</div>
          <Link
            to="/sessions"
            className="text-sm font-semibold text-[var(--brand)] transition hover:text-[var(--brand-strong)]"
          >
            View all
          </Link>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead className="bg-[var(--panel-soft)] text-left text-[var(--muted)]">
            <tr>
              <th className="px-6 py-3 font-medium">Session</th>
              <th className="px-6 py-3 font-medium">Status</th>
              <th className="px-6 py-3 font-medium">Topics</th>
              <th className="px-6 py-3 font-medium">Elapsed</th>
              <th className="px-6 py-3 font-medium">Queued</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr
                key={item.session_id}
                className="border-t border-[var(--line)] transition hover:bg-white/70"
              >
                <td className="px-6 py-4">
                  <Link to={`/sessions/${item.session_id}`} className="font-semibold text-[var(--ink)] hover:text-[var(--brand)]">
                    {item.session_id.slice(0, 8)}
                  </Link>
                </td>
                <td className="px-6 py-4">
                  <Badge tone={getStatusTone(item.status) as never}>{item.status}</Badge>
                </td>
                <td className="px-6 py-4 text-[var(--muted)]">
                  {(item.requested_topics || []).slice(0, 2).join(", ") || "—"}
                </td>
                <td className="px-6 py-4 text-[var(--muted)]">{formatMinutes(item.elapsed_minutes)}</td>
                <td className="px-6 py-4 text-[var(--muted)]">{formatDate(item.queued_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
