import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";

export type SessionFiltersValue = {
  status: string;
  ads: string;
  search: string;
  pageSize: number;
};

export function SessionFilters({
  value,
  onChange,
  onReset,
}: {
  value: SessionFiltersValue;
  onChange: (next: SessionFiltersValue) => void;
  onReset: () => void;
}) {
  return (
    <div className="panel grid gap-4 p-5 lg:grid-cols-[1fr_180px_180px_112px_auto]">
      <Input
        label="Search"
        placeholder="session id, topic, status..."
        value={value.search}
        onChange={(event) => onChange({ ...value, search: event.target.value })}
      />
      <Select
        label="Status"
        value={value.status}
        onChange={(event) => onChange({ ...value, status: event.target.value })}
      >
        <option value="">All</option>
        <option value="queued">Queued</option>
        <option value="running">Running</option>
        <option value="completed">Completed</option>
        <option value="failed">Failed</option>
        <option value="stopped">Stopped</option>
      </Select>
      <Select
        label="Ads"
        value={value.ads}
        onChange={(event) => onChange({ ...value, ads: event.target.value })}
      >
        <option value="">All</option>
        <option value="with_ads">With ads</option>
        <option value="without_ads">Without ads</option>
        <option value="video_captures">With video captures</option>
      </Select>
      <Select
        label="Page size"
        className="min-w-0"
        value={String(value.pageSize)}
        onChange={(event) => onChange({ ...value, pageSize: Number(event.target.value) })}
      >
        <option value="10">10</option>
        <option value="25">25</option>
        <option value="50">50</option>
      </Select>
      <Button type="button" variant="ghost" onClick={onReset} className="mt-auto w-full">
        Reset
      </Button>
    </div>
  );
}
