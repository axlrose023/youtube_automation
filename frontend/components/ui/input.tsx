import clsx from "clsx";
import type { InputHTMLAttributes } from "react";

type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  label?: string;
  hint?: string;
};

export function Input({ className, label, hint, ...props }: InputProps) {
  return (
    <label className="flex flex-col gap-2 text-sm text-[var(--muted)]">
      {label ? <span className="font-medium text-[var(--ink)]">{label}</span> : null}
      <input
        className={clsx(
          "rounded-2xl border border-[var(--line)] bg-white px-4 py-3 text-sm text-[var(--ink)] outline-none transition placeholder:text-slate-400 focus:border-[var(--brand)] focus:ring-4 focus:ring-emerald-100",
          className,
        )}
        {...props}
      />
      {hint ? <span className="text-xs text-[var(--muted)]">{hint}</span> : null}
    </label>
  );
}
