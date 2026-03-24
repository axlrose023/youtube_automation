import clsx from "clsx";
import { LayoutDashboard, LogOut, PlayCircle, ShieldUser, Tv2, UserCircle2 } from "lucide-react";
import { NavLink, useLocation } from "react-router-dom";

import { useAuth } from "@/lib/auth-context";

const items = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/sessions", label: "Sessions", icon: PlayCircle },
];

export function Sidebar() {
  const location = useLocation();
  const { logout, user } = useAuth();

  const finalItems = user?.is_admin
    ? [...items, { href: "/users", label: "Users", icon: ShieldUser }]
    : items;

  return (
    <aside className="flex h-screen w-72 shrink-0 flex-col bg-[var(--nav)] px-5 py-6 text-white">
      <div className="mb-8 flex items-center gap-3 rounded-3xl bg-white/5 px-4 py-4">
        <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--brand)] text-white shadow-[0_18px_40px_rgba(214,82,82,0.28)]">
          <Tv2 size={20} />
        </div>
        <div>
          <div className="text-xs uppercase tracking-[0.24em] text-white/55">Control</div>
          <div className="text-lg font-semibold">YouTube Ops</div>
        </div>
      </div>

      <nav className="space-y-2">
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
                "flex items-center gap-3 rounded-2xl px-4 py-3 text-sm font-medium transition",
                active
                  ? "bg-white text-slate-900 shadow-[0_14px_30px_rgba(255,255,255,0.12)]"
                  : "text-white/78 hover:bg-white/6 hover:text-white",
              )}
            >
              <Icon size={18} className={active ? "text-slate-900" : "text-white/80"} />
              <span className={active ? "text-slate-900" : "text-white/90"}>{item.label}</span>
            </NavLink>
          );
        })}
      </nav>

      <div className="mt-auto rounded-3xl border border-white/8 bg-white/5 px-4 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-white/10 text-white">
            <UserCircle2 size={20} />
          </div>
          <div className="min-w-0">
            <div className="text-xs uppercase tracking-[0.2em] text-white/45">Signed in</div>
            <div className="truncate text-sm font-semibold text-white/90">{user?.username}</div>
          </div>
        </div>
        <button
          onClick={logout}
          className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-2xl border border-white/10 bg-white/8 px-4 py-3 text-sm font-semibold text-white/90 transition hover:bg-white/12"
        >
          <LogOut size={16} />
          Logout
        </button>
      </div>
    </aside>
  );
}
