import { Card } from "@/components/ui/card";
import type { LucideIcon } from "lucide-react";

export function MetricCard({
  label,
  value,
  note,
  icon: Icon,
  color = "var(--brand)",
}: {
  label: string;
  value: string;
  note: string;
  icon?: LucideIcon;
  accent?: string;
  color?: string;
}) {
  return (
    <Card className="group relative overflow-hidden">
      <div
        className="absolute -right-6 -top-6 h-24 w-24 rounded-full opacity-10 blur-2xl transition-opacity group-hover:opacity-20"
        style={{ background: color }}
      />
      <div className="relative">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium uppercase tracking-wider text-[var(--muted)]">{label}</span>
          {Icon ? <Icon size={16} className="text-[var(--muted)]" /> : null}
        </div>
        <div className="mt-2 text-2xl font-semibold text-[var(--ink)]">{value}</div>
        <div className="mt-1 text-xs text-[var(--muted)]">{note}</div>
      </div>
    </Card>
  );
}
