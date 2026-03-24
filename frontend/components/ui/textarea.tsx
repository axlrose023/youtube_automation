import clsx from "clsx";
import type { TextareaHTMLAttributes } from "react";

type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  label?: string;
  hint?: string;
};

export function Textarea({ className, label, hint, ...props }: TextareaProps) {
  return (
    <label className="flex flex-col gap-1.5 text-sm">
      {label ? <span className="font-medium text-[var(--ink-secondary)]">{label}</span> : null}
      <textarea
        className={clsx(
          "min-h-[100px] rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2.5 text-sm text-[var(--ink)] outline-none transition-all placeholder:text-[var(--muted)] focus:border-[var(--brand)] focus:ring-2 focus:ring-[var(--brand-soft)]",
          className,
        )}
        {...props}
      />
      {hint ? <span className="text-xs text-[var(--muted)]">{hint}</span> : null}
    </label>
  );
}
