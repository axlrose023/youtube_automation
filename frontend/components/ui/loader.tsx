export function Loader({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex min-h-[240px] flex-col items-center justify-center gap-4 rounded-[28px] border border-[var(--line)] bg-white/80">
      <div className="h-12 w-12 animate-spin rounded-full border-4 border-slate-200 border-t-[var(--brand)]" />
      <div className="text-sm font-medium text-[var(--muted)]">{label}</div>
    </div>
  );
}
