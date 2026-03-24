import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="panel max-w-xl p-10 text-center">
        <div className="text-sm uppercase tracking-[0.24em] text-[var(--muted)]">404</div>
        <h1 className="mt-4 text-4xl font-semibold text-[var(--ink)]">Page not found</h1>
        <p className="mt-4 text-[var(--muted)]">
          The route does not exist in this admin panel.
        </p>
        <Link
          to="/dashboard"
          className="mt-6 inline-flex rounded-2xl bg-[var(--brand)] px-5 py-3 text-sm font-semibold text-white"
        >
          Back to dashboard
        </Link>
      </div>
    </main>
  );
}
