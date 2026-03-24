import clsx from "clsx";
import type { InputHTMLAttributes } from "react";

type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  label?: string;
  hint?: string;
};

export function Input({ className, label, hint, ...props }: InputProps) {
  return (
    <label className="flex flex-col gap-1.5 text-sm">
      {label ? <span className="font-medium text-[var(--ink-secondary)]">{label}</span> : null}
      <input
        className={clsx(
          "rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2.5 text-sm text-[var(--ink)] outline-none transition-all placeholder:text-[var(--muted)] focus:border-[var(--brand)] focus:ring-2 focus:ring-[var(--brand-soft)]",
          className,
        )}
        {...props}
      />
      {hint ? <span className="text-xs text-[var(--muted)]">{hint}</span> : null}
    </label>
  );
}
