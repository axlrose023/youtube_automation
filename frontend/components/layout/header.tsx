import { useLocation } from "react-router-dom";

const routeMeta = [
  {
    match: (pathname: string) => pathname.startsWith("/sessions/"),
    eyebrow: "Session review",
    title: "Inspect runtime, ads, and media outcomes",
  },
  {
    match: (pathname: string) => pathname === "/sessions",
    eyebrow: "History",
    title: "Audit completed runs and monitor active sessions",
  },
  {
    match: (pathname: string) => pathname === "/users",
    eyebrow: "Access control",
    title: "Manage operator accounts and permissions",
  },
  {
    match: () => true,
    eyebrow: "Command center",
    title: "Monitor emulations, captures, and relevance signals",
  },
] as const;

export function Header() {
  const location = useLocation();
  const meta =
    routeMeta.find((item) => item.match(location.pathname)) ?? routeMeta[routeMeta.length - 1];

  return (
    <header className="border-b border-[var(--line)] bg-white/52 px-6 py-6 backdrop-blur-xl lg:px-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="section-eyebrow">
            {meta.eyebrow}
          </div>
          <h1 className="mt-1 text-2xl font-semibold text-[var(--ink)]">
            {meta.title}
          </h1>
        </div>
        <div className="info-chip border-[rgba(23,32,51,0.08)] bg-white/75 text-[var(--ink)]">
          <span className="inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500 shadow-[0_0_0_6px_rgba(16,185,129,0.14)]" />
          Local workspace
        </div>
      </div>
    </header>
  );
}
