import { RotateCcw } from "lucide-react";

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
    <div className="panel grid grid-cols-2 gap-3 p-4 md:grid-cols-[1fr_160px_160px_100px_auto]">
      <Input
        label="Поиск"
        placeholder="id сессии, тема, статус..."
        className="col-span-2 md:col-span-1"
        value={value.search}
        onChange={(event) => onChange({ ...value, search: event.target.value })}
      />
      <Select
        label="Статус"
        value={value.status}
        onChange={(event) => onChange({ ...value, status: event.target.value })}
      >
        <option value="">Все</option>
        <option value="queued">В очереди</option>
        <option value="running">Запущена</option>
        <option value="completed">Завершена</option>
        <option value="failed">Ошибка</option>
        <option value="stopped">Остановлена</option>
      </Select>
      <Select
        label="Реклама"
        value={value.ads}
        onChange={(event) => onChange({ ...value, ads: event.target.value })}
      >
        <option value="">Все</option>
        <option value="with_ads">С рекламой</option>
        <option value="without_ads">Без рекламы</option>
        <option value="video_captures">С видеозаписью</option>
      </Select>
      <Select
        label="На странице"
        className="min-w-0"
        value={String(value.pageSize)}
        onChange={(event) => onChange({ ...value, pageSize: Number(event.target.value) })}
      >
        <option value="10">10</option>
        <option value="25">25</option>
        <option value="50">50</option>
      </Select>
      <Button type="button" variant="ghost" onClick={onReset} className="mt-auto w-full gap-1.5">
        <RotateCcw size={13} />
        Сбросить
      </Button>
    </div>
  );
}
