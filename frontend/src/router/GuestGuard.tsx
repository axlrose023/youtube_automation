import { Navigate } from "react-router-dom";

import { Loader } from "@/components/ui/loader";
import { useAuth } from "@/lib/auth-context";

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
