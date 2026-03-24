import clsx from "clsx";
import { LayoutDashboard, LogOut, PlayCircle, ShieldUser, Tv2, UserCircle2 } from "lucide-react";
import { NavLink, useLocation } from "react-router-dom";

import { useAuth } from "@/lib/auth-context";

const items = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/sessions", label: "Sessions", icon: PlayCircle },
];

export function Header() {
  const location = useLocation();
  const { logout, user } = useAuth();

  const finalItems = user?.is_admin
    ? [...items, { href: "/users", label: "Users", icon: ShieldUser }]
    : items;

  return (
    <header className="sticky top-0 z-40 border-b border-[var(--line)] bg-white/80 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-[1400px] items-center justify-between gap-6 px-6">
        <div className="flex items-center gap-8">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[var(--brand)] text-white">
              <Tv2 size={16} />
            </div>
            <span className="text-sm font-semibold text-[var(--ink)]">YouTube Ops</span>
          </div>

          <nav className="flex items-center gap-1">
            {finalItems.map((item) => {
              const Icon = item.icon;
              const active =
                location.pathname === item.href ||
                location.pathname.startsWith(`${item.href}/`);
              return (
                <NavLink
                  key={item.href}
                  to={item.href}
                  className={clsx(
                    "flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition-all",
                    active
                      ? "bg-[var(--brand-soft)] text-[var(--brand)]"
                      : "text-[var(--muted)] hover:bg-[var(--panel-hover)] hover:text-[var(--ink)]",
                  )}
                >
                  <Icon size={15} />
                  {item.label}
                </NavLink>
              );
            })}
          </nav>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 rounded-lg bg-[var(--panel-soft)] px-3 py-1.5">
            <UserCircle2 size={15} className="text-[var(--muted)]" />
            <span className="text-sm text-[var(--ink-secondary)]">{user?.username}</span>
          </div>
          <button
            onClick={logout}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-[var(--muted)] transition hover:bg-[var(--danger-soft)] hover:text-[var(--danger)]"
            title="Logout"
          >
            <LogOut size={15} />
          </button>
        </div>
      </div>
    </header>
  );
}
