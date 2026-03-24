import { Navigate } from "react-router-dom";

import { useAuth } from "@/lib/auth-context";
import { Loader } from "@/components/ui/loader";

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();

  if (loading) {
    return <Loader label="Loading session" />;
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}
