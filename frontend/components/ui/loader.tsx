export function Loader({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex min-h-[200px] flex-col items-center justify-center gap-4 rounded-xl border border-[var(--line)] bg-[var(--panel)]">
      <div className="relative h-10 w-10">
        <div className="absolute inset-0 animate-spin rounded-full border-2 border-[var(--line)] border-t-[var(--brand)]" />
        <div className="absolute inset-1.5 animate-spin rounded-full border-2 border-[var(--line)] border-b-[var(--accent)]" style={{ animationDirection: "reverse", animationDuration: "0.8s" }} />
      </div>
      <div className="text-sm text-[var(--muted)]">{label}</div>
    </div>
  );
}
