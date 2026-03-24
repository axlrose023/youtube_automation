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
    <aside className="flex h-screen w-72 shrink-0 flex-col border-r border-white/8 bg-[linear-gradient(180deg,#172033_0%,#182135_55%,#1f1d2d_100%)] px-5 py-6 text-white shadow-[24px_0_80px_rgba(23,32,51,0.18)]">
      <div className="mb-8 rounded-[2rem] border border-white/8 bg-white/6 p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]">
        <div className="flex items-center gap-3">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--brand)] text-white shadow-[0_18px_40px_rgba(214,82,82,0.28)]">
            <Tv2 size={20} />
          </div>
          <div>
            <div className="text-xs uppercase tracking-[0.24em] text-white/45">Control</div>
            <div className="text-lg font-semibold">YouTube Ops</div>
          </div>
        </div>
        <div className="mt-4 rounded-2xl border border-white/8 bg-black/12 px-3 py-3 text-xs leading-5 text-white/68">
          Review emulation health, ad capture quality, and relevance verdicts in one surface.
        </div>
      </div>

      <div className="mb-3 px-2 text-[0.68rem] font-semibold uppercase tracking-[0.24em] text-white/35">
        Navigation
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
                  ? "bg-white text-slate-900 shadow-[0_18px_36px_rgba(255,255,255,0.12)]"
                  : "text-white/72 hover:bg-white/6 hover:text-white",
              )}
            >
              <Icon size={18} className={active ? "text-slate-900" : "text-white/80"} />
              <span className={active ? "text-slate-900" : "text-white/90"}>{item.label}</span>
            </NavLink>
          );
        })}
      </nav>

      <div className="mt-auto rounded-[2rem] border border-white/8 bg-white/6 px-4 py-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]">
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
          className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-2xl border border-white/10 bg-black/12 px-4 py-3 text-sm font-semibold text-white/90 transition hover:bg-white/12"
        >
          <LogOut size={16} />
          Logout
        </button>
      </div>
    </aside>
  );
}
