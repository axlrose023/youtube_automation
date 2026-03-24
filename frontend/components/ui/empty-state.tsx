import type { ReactNode } from "react";

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="panel flex min-h-[220px] flex-col items-center justify-center gap-4 p-8 text-center">
      <div className="text-lg font-semibold text-[var(--ink)]">{title}</div>
      <div className="max-w-lg text-sm leading-6 text-[var(--muted)]">{description}</div>
      {action}
    </div>
  );
}
