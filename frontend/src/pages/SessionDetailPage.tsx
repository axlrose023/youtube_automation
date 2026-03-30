import { useParams } from "react-router-dom";

import { EmptyState } from "@/components/ui/empty-state";
import { SessionDetailScreen } from "@/components/sessions/session-detail-screen";

export function SessionDetailPage() {
  const { sessionId } = useParams();

  if (!sessionId) {
    return (
      <EmptyState
        title="Сессия не указана"
        description="В маршруте не передан идентификатор сессии."
      />
    );
  }

  return <SessionDetailScreen sessionId={sessionId} />;
}
