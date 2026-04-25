import clsx from "clsx";
import { Globe, LayoutDashboard, LogOut, Megaphone, Menu, Monitor, PlayCircle, ShieldUser, Tv2, UserCircle2, X } from "lucide-react";
import { useState } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { useAuth } from "@/lib/auth-context";

const items = [
  { href: "/dashboard", label: "Дашборд", icon: LayoutDashboard },
  { href: "/sessions", label: "Сессии", icon: PlayCircle },
  { href: "/ads", label: "Реклама", icon: Megaphone },
  { href: "/proxies", label: "Прокси", icon: Globe },
];

export function Header() {
  const location = useLocation();
  const { logout, user } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);

  const finalItems = user?.is_admin
    ? [
        ...items,
        { href: "/users", label: "Пользователи", icon: ShieldUser },
        { href: "/setup", label: "Настройка", icon: Monitor },
      ]
    : items;

  return (
    <header className="sticky top-0 z-40 border-b border-[var(--line)] bg-white/80 backdrop-blur-xl">
      <div className="mx-auto flex h-14 max-w-[1400px] items-center justify-between gap-4 px-4 md:h-16 md:gap-6 md:px-6">
        <div className="flex items-center gap-4 md:gap-8">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[var(--brand)] text-white">
              <Tv2 size={16} />
            </div>
            <span className="text-sm font-semibold text-[var(--ink)]">YouTube Emulator</span>
          </div>

          <nav className="hidden items-center gap-1 md:flex">
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

        <div className="flex items-center gap-2 md:gap-3">
          <div className="hidden items-center gap-2 rounded-lg bg-[var(--panel-soft)] px-3 py-1.5 sm:flex">
            <UserCircle2 size={15} className="text-[var(--muted)]" />
            <span className="text-sm text-[var(--ink-secondary)]">{user?.username}</span>
          </div>
          <button
            onClick={logout}
            className="hidden h-8 w-8 items-center justify-center rounded-lg text-[var(--muted)] transition hover:bg-[var(--danger-soft)] hover:text-[var(--danger)] md:flex"
            title="Выйти"
          >
            <LogOut size={15} />
          </button>
          <button
            onClick={() => setMobileOpen((o) => !o)}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-[var(--muted)] transition hover:bg-[var(--panel-hover)] md:hidden"
          >
            {mobileOpen ? <X size={18} /> : <Menu size={18} />}
          </button>
        </div>
      </div>

      {/* Mobile dropdown */}
      {mobileOpen && (
        <div className="border-t border-[var(--line)] bg-white px-4 pb-4 pt-2 md:hidden">
          <nav className="flex flex-col gap-1">
            {finalItems.map((item) => {
              const Icon = item.icon;
              const active =
                location.pathname === item.href ||
                location.pathname.startsWith(`${item.href}/`);
              return (
                <NavLink
                  key={item.href}
                  to={item.href}
                  onClick={() => setMobileOpen(false)}
                  className={clsx(
                    "flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm font-medium transition-all",
                    active
                      ? "bg-[var(--brand-soft)] text-[var(--brand)]"
                      : "text-[var(--muted)] hover:bg-[var(--panel-hover)] hover:text-[var(--ink)]",
                  )}
                >
                  <Icon size={16} />
                  {item.label}
                </NavLink>
              );
            })}
          </nav>
          <div className="mt-3 flex items-center justify-between border-t border-[var(--line)] pt-3">
            <div className="flex items-center gap-2">
              <UserCircle2 size={15} className="text-[var(--muted)]" />
              <span className="text-sm text-[var(--ink-secondary)]">{user?.username}</span>
            </div>
            <button
              onClick={logout}
              className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-[var(--danger)] transition hover:bg-[var(--danger-soft)]"
            >
              <LogOut size={14} />
              Выйти
            </button>
          </div>
        </div>
      )}
    </header>
  );
}
