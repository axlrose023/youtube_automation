import clsx from "clsx";
import type { SelectHTMLAttributes } from "react";
import { ChevronDown } from "lucide-react";

type SelectProps = SelectHTMLAttributes<HTMLSelectElement> & {
  label?: string;
};

export function Select({ className, label, children, ...props }: SelectProps) {
  return (
    <label className="flex flex-col gap-1.5 text-sm">
      {label ? <span className="font-medium text-[var(--ink-secondary)]">{label}</span> : null}
      <div className="relative">
        <select
          className={clsx(
            "w-full appearance-none rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2.5 pr-10 text-sm text-[var(--ink)] outline-none transition-all focus:border-[var(--brand)] focus:ring-2 focus:ring-[var(--brand-soft)]",
            className,
          )}
          {...props}
        >
          {children}
        </select>
        <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-[var(--muted)]">
          <ChevronDown size={15} strokeWidth={2.2} />
        </span>
      </div>
    </label>
  );
}
