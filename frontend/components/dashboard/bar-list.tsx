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
    <Card className="p-5">
      <div className="mb-5 text-sm font-semibold text-[var(--ink)]">{title}</div>
      <div className="space-y-3">
        {items.map((item) => (
          <div key={item.label}>
            <div className="mb-1.5 flex items-center justify-between gap-4 text-xs">
              <span className="truncate text-[var(--ink-secondary)]">{item.label}</span>
              <span className="font-medium text-[var(--ink)]">{formatNumber(item.value)}</span>
            </div>
            <div className="h-1.5 rounded-full bg-[var(--bg-soft)]">
              <div
                className={`h-1.5 rounded-full ${colorClass} transition-all`}
                style={{ width: `${Math.max((item.value / max) * 100, 6)}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}
