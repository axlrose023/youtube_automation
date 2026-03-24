import { Navigate } from "react-router-dom";

import { useAuth } from "@/lib/auth-context";
import { Loader } from "@/components/ui/loader";

export function GuestGuard({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();

  if (loading) {
    return <Loader label="Checking auth" />;
  }

  if (user) {
    return <Navigate to="/dashboard" replace />;
  }

  return <>{children}</>;
}
