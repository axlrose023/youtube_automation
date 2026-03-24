import { useParams } from "react-router-dom";

import { EmptyState } from "@/components/ui/empty-state";
import { SessionDetailScreen } from "@/components/sessions/session-detail-screen";

export function SessionDetailPage() {
  const { sessionId } = useParams();

  if (!sessionId) {
    return (
      <EmptyState
        title="Session is missing"
        description="No session id was provided in the route."
      />
    );
  }

  return <SessionDetailScreen sessionId={sessionId} />;
}
