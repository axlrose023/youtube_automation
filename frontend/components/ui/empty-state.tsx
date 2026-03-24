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
    <div className="panel flex min-h-[200px] flex-col items-center justify-center gap-3 p-8 text-center">
      <div className="text-base font-semibold text-[var(--ink)]">{title}</div>
      <div className="max-w-md text-sm leading-relaxed text-[var(--muted)]">{description}</div>
      {action}
    </div>
  );
}
