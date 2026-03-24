import { Card } from "@/components/ui/card";
import { formatNumber } from "@/lib/format";

export function BarList({
  title,
  items,
  colorClass,
}: {
  title: string;
  items: Array<{ label: string; value: number }>;
  colorClass: string;
}) {
  const max = items[0]?.value || 1;

  return (
    <Card className="p-6">
      <div className="mb-6 text-lg font-semibold text-[var(--ink)]">{title}</div>
      <div className="space-y-4">
        {items.map((item) => (
          <div key={item.label}>
            <div className="mb-2 flex items-center justify-between gap-4 text-sm">
              <span className="truncate text-[var(--ink)]">{item.label}</span>
              <span className="font-semibold text-[var(--muted)]">{formatNumber(item.value)}</span>
            </div>
            <div className="h-2 rounded-full bg-slate-100">
              <div
                className={`h-2 rounded-full ${colorClass}`}
                style={{ width: `${Math.max((item.value / max) * 100, 8)}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}
