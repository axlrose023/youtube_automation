export function Header() {
  return (
    <header className="border-b border-[var(--line)] px-8 py-6">
      <div>
        <div className="text-xs uppercase tracking-[0.24em] text-[var(--muted)]">
          Admin panel
        </div>
        <h1 className="mt-1 text-2xl font-semibold text-[var(--ink)]">
          Emulation Control Surface
        </h1>
      </div>
    </header>
  );
}
