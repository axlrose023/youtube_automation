import clsx from "clsx";

type BadgeTone = "neutral" | "success" | "warning" | "danger" | "info";

const toneClasses: Record<BadgeTone, string> = {
  neutral: "bg-[var(--panel-soft)] text-[var(--ink-secondary)] border-[var(--line)]",
  success: "bg-[var(--accent-soft)] text-[var(--accent)] border-[var(--accent)]/15",
  warning: "bg-[var(--warning-soft)] text-[var(--warning)] border-[var(--warning)]/15",
  danger: "bg-[var(--danger-soft)] text-[var(--danger)] border-[var(--danger)]/15",
  info: "bg-[var(--info-soft)] text-[var(--info)] border-[var(--info)]/15",
};

export function Badge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: BadgeTone;
}) {
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium",
        toneClasses[tone],
      )}
    >
      {children}
    </span>
  );
}
