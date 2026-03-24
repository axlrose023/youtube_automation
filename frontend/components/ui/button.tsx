import clsx from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  loading?: boolean;
  children: ReactNode;
};

const variants = {
  primary:
    "bg-[var(--brand)] text-white shadow-[0_4px_14px_rgba(108,92,231,0.25)] hover:bg-[var(--brand-strong)] hover:shadow-[0_4px_20px_rgba(108,92,231,0.3)]",
  secondary:
    "bg-[var(--panel-soft)] text-[var(--ink)] border border-[var(--line)] hover:bg-[var(--line)] hover:border-[var(--line-strong)]",
  ghost:
    "bg-transparent text-[var(--ink-secondary)] border border-[var(--line)] hover:bg-[var(--panel-soft)] hover:text-[var(--ink)] hover:border-[var(--line-strong)]",
  danger:
    "bg-[var(--danger)] text-white shadow-[0_4px_14px_rgba(231,76,60,0.2)] hover:bg-[#d44332]",
};

export function Button({
  variant = "primary",
  className,
  loading,
  disabled,
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      className={clsx(
        "inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-40",
        variants[variant],
        className,
      )}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? (
        <>
          <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
          Working...
        </>
      ) : (
        children
      )}
    </button>
  );
}
