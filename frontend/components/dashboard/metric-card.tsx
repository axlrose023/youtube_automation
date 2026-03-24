import type { LucideIcon } from "lucide-react";

import { Card } from "@/components/ui/card";

export function MetricCard({
  label,
  value,
  note,
  accent = "bg-emerald-50 text-emerald-700",
  icon: Icon,
}: {
  label: string;
  value: string;
  note: string;
  accent?: string;
  icon: LucideIcon;
}) {
  return (
    <Card className="relative overflow-hidden p-0">
      <div className="absolute inset-x-6 top-0 h-px bg-[linear-gradient(90deg,transparent,rgba(214,82,82,0.24),transparent)]" />
      <div className="flex items-start justify-between gap-4 p-6">
        <div>
          <div className="section-eyebrow">{label}</div>
          <div className="mt-3 text-4xl font-semibold text-[var(--ink)]">{value}</div>
          <div className="mt-2 max-w-[18rem] text-sm leading-6 text-[var(--muted)]">{note}</div>
          <div className={`mt-5 h-2 w-20 rounded-full ${accent} opacity-80`} />
        </div>
        <div className={`flex h-14 w-14 items-center justify-center rounded-2xl ${accent} shadow-[inset_0_1px_0_rgba(255,255,255,0.45)]`}>
          <Icon size={18} />
        </div>
      </div>
    </Card>
  );
}
