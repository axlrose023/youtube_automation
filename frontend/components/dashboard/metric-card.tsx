import { Card } from "@/components/ui/card";

export function MetricCard({
  label,
  value,
  note,
  accent = "bg-emerald-50 text-emerald-700",
}: {
  label: string;
  value: string;
  note: string;
  accent?: string;
}) {
  return (
    <Card className="relative overflow-hidden">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-xs uppercase tracking-[0.24em] text-[var(--muted)]">{label}</div>
          <div className="mt-3 text-3xl font-semibold text-[var(--ink)]">{value}</div>
          <div className="mt-2 text-sm text-[var(--muted)]">{note}</div>
        </div>
        <div className={`h-14 w-14 rounded-full ${accent} opacity-80`} />
      </div>
    </Card>
  );
}
