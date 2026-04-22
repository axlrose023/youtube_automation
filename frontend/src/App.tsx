import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider } from "@/lib/auth-context";
import { AuthGuard } from "@/src/router/AuthGuard";
import { GuestGuard } from "@/src/router/GuestGuard";
import { AdsPage } from "@/src/pages/AdsPage";
import { DashboardPage } from "@/src/pages/DashboardPage";
import { LoginPage } from "@/src/pages/LoginPage";
import { NotFoundPage } from "@/src/pages/NotFoundPage";
import { SessionsPage } from "@/src/pages/SessionsPage";
import { SessionDetailPage } from "@/src/pages/SessionDetailPage";
import { ProxiesPage } from "@/src/pages/ProxiesPage";
import { UsersPage } from "@/src/pages/UsersPage";
import { DashboardLayout } from "@/src/router/DashboardLayout";

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route
            path="/login"
            element={
              <GuestGuard>
                <LoginPage />
              </GuestGuard>
            }
          />
          <Route
            path="/"
            element={
              <AuthGuard>
                <DashboardLayout />
              </AuthGuard>
            }
          >
            <Route index element={<Navigate to="/dashboard" replace />} />
            <Route path="dashboard" element={<DashboardPage />} />
            <Route path="sessions" element={<SessionsPage />} />
            <Route path="sessions/:sessionId" element={<SessionDetailPage />} />
            <Route path="ads" element={<AdsPage />} />
            <Route path="proxies" element={<ProxiesPage />} />
            <Route path="users" element={<UsersPage />} />
          </Route>
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
