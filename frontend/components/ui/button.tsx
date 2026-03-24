import clsx from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  loading?: boolean;
  children: ReactNode;
};

const variants = {
  primary:
    "bg-[var(--brand)] text-white shadow-[0_14px_30px_rgba(214,82,82,0.22)] hover:bg-[var(--brand-strong)]",
  secondary:
    "bg-[var(--ink)] text-white shadow-[0_14px_30px_rgba(23,32,51,0.14)] hover:bg-[#24304a]",
  ghost:
    "bg-white text-[var(--ink)] border border-[var(--line)] hover:bg-[var(--panel-soft)]",
  danger:
    "bg-[var(--danger)] text-white shadow-[0_14px_30px_rgba(217,95,95,0.18)] hover:bg-[#c54f4f]",
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
        "inline-flex items-center justify-center gap-2 rounded-2xl px-4 py-2.5 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-60",
        variants[variant],
        className,
      )}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? "Working..." : children}
    </button>
  );
}
