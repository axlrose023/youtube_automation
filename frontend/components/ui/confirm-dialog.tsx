import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

type ConfirmDialogProps = {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  confirmTone?: "default" | "danger";
  loading?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
};

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  confirmTone = "default",
  loading = false,
  onCancel,
  onConfirm,
}: ConfirmDialogProps) {
  if (!open) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4 backdrop-blur-sm">
      <Card className="w-full max-w-md p-6 shadow-2xl" glow>
        <div className="text-base font-semibold text-[var(--ink)]">{title}</div>
        <p className="mt-2 text-sm leading-relaxed text-[var(--muted)]">{description}</p>
        <div className="mt-6 flex justify-end gap-3">
          <Button type="button" variant="ghost" onClick={onCancel} disabled={loading}>
            Cancel
          </Button>
          <Button
            type="button"
            variant={confirmTone === "danger" ? "danger" : "primary"}
            loading={loading}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      </Card>
    </div>
  );
}
