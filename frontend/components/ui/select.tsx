import clsx from "clsx";
import type { SelectHTMLAttributes } from "react";
import { ChevronDown } from "lucide-react";

type SelectProps = SelectHTMLAttributes<HTMLSelectElement> & {
  label?: string;
};

export function Select({ className, label, children, ...props }: SelectProps) {
  return (
    <label className="flex flex-col gap-2 text-sm text-[var(--muted)]">
      {label ? <span className="font-medium text-[var(--ink)]">{label}</span> : null}
      <div className="relative">
        <select
          className={clsx(
            "w-full appearance-none rounded-2xl border border-[var(--line)] bg-white px-4 py-3 pr-12 text-sm text-[var(--ink)] outline-none transition focus:border-[var(--brand)] focus:ring-4 focus:ring-rose-100",
            className,
          )}
          {...props}
        >
          {children}
        </select>
        <span className="pointer-events-none absolute inset-y-0 right-4 flex items-center text-[var(--muted)]">
          <ChevronDown size={18} strokeWidth={2.1} />
        </span>
      </div>
    </label>
  );
}
