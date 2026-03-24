import { Outlet } from "react-router-dom";

import { AppShell } from "@/components/layout/app-shell";

export function DashboardLayout() {
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}
