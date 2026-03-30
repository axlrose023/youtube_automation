import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { formatDate, formatMinutes } from "@/lib/format";
import { formatSessionStatus, getStatusTone } from "@/lib/metrics";
import type { EmulationHistoryItem } from "@/types/api";

export function RecentSessions({ items }: { items: EmulationHistoryItem[] }) {
  return (
    <Card className="overflow-hidden p-0">
      <div className="border-b border-[var(--line)] px-5 py-4">
        <div className="text-sm font-semibold text-[var(--ink)]">Последние сессии</div>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--line)] text-left text-xs text-[var(--muted)]">
              <th className="px-5 py-3 font-medium">Сессия</th>
              <th className="px-5 py-3 font-medium">Статус</th>
              <th className="px-5 py-3 font-medium">Запрошенные темы</th>
              <th className="px-5 py-3 font-medium">Прошло</th>
              <th className="px-5 py-3 font-medium">Поставлена в очередь</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.session_id} className="border-t border-[var(--line)] transition-colors hover:bg-[var(--panel-hover)]">
                <td className="px-5 py-3">
                  <Link to={`/sessions/${item.session_id}`} className="font-medium text-[var(--ink)] transition hover:text-[var(--brand)]">
                    {item.session_id.slice(0, 8)}
                  </Link>
                </td>
                <td className="px-5 py-3">
                  <Badge tone={getStatusTone(item.status) as never}>{formatSessionStatus(item.status)}</Badge>
                </td>
                <td className="px-5 py-3 text-[var(--muted)]">
                  {(item.requested_topics || []).join(", ") || "—"}
                </td>
                <td className="px-5 py-3 text-[var(--muted)]">{formatMinutes(item.elapsed_minutes)}</td>
                <td className="px-5 py-3 text-[var(--muted)]">{formatDate(item.queued_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
