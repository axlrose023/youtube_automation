import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="panel panel-glow max-w-md p-10 text-center">
        <div className="text-xs font-medium uppercase tracking-wider text-[var(--muted)]">404</div>
        <h1 className="mt-3 text-3xl font-semibold text-[var(--ink)]">Page not found</h1>
        <p className="mt-3 text-sm text-[var(--muted)]">
          The route does not exist in this admin panel.
        </p>
        <Link
          to="/dashboard"
          className="mt-6 inline-flex rounded-lg bg-[var(--brand)] px-4 py-2 text-sm font-medium text-white shadow-[0_4px_14px_rgba(108,92,231,0.25)] transition hover:bg-[var(--brand-strong)]"
        >
          Back to dashboard
        </Link>
      </div>
    </main>
  );
}
