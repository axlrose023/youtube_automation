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
      <div className="mb-6 flex items-center justify-between gap-4">
        <div>
          <div className="section-eyebrow">Signal distribution</div>
          <div className="mt-1 text-lg font-semibold text-[var(--ink)]">{title}</div>
        </div>
        <div className="rounded-full bg-[var(--panel-soft)] px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-[var(--muted)]">
          Top 5
        </div>
      </div>
      <div className="space-y-4">
        {items.map((item, index) => (
          <div key={item.label}>
            <div className="mb-2 flex items-center justify-between gap-4 text-sm">
              <div className="flex min-w-0 items-center gap-3">
                <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--panel-soft)] text-xs font-semibold text-[var(--muted)]">
                  {index + 1}
                </span>
                <span className="truncate text-[var(--ink)]">{item.label}</span>
              </div>
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
