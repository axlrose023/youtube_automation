import { useEffect, useState } from "react";
import { Rocket, Trash2 } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { getProxies, startEmulation } from "@/lib/api";
import type { Proxy } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";

export function SessionLauncher() {
  const [duration, setDuration] = useState("30");
  const [topics, setTopics] = useState([""]);
  const [proxyId, setProxyId] = useState("");
  const [proxies, setProxies] = useState<Proxy[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    void getProxies(true)
      .then((data) => {
        setProxies(data.items);
        if (data.items.length > 0 && !proxyId) {
          setProxyId(data.items[0].id);
        }
      })
      .catch(() => {});
  }, []);

  function normalizeTopics(next: string[]) {
    const normalized = [...next];
    while (
      normalized.length > 1 &&
      !normalized[normalized.length - 1]?.trim() &&
      !normalized[normalized.length - 2]?.trim()
    ) {
      normalized.pop();
    }
    if (normalized.length === 0) return [""];
    if (normalized.every((item) => item.trim())) normalized.push("");
    return normalized;
  }

  function updateTopic(index: number, value: string) {
    setTopics((prev) =>
      normalizeTopics(prev.map((item, i) => (i === index ? value : item))),
    );
  }

  function removeTopic(index: number) {
    setTopics((prev) => normalizeTopics(prev.filter((_, i) => i !== index)));
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const payloadTopics = topics.map((t) => t.trim()).filter(Boolean);
      if (!duration.trim()) {
        setError("Нужно указать длительность.");
        setLoading(false);
        return;
      }
      if (payloadTopics.length === 0) {
        setError("Нужна хотя бы одна тема.");
        setLoading(false);
        return;
      }
      if (!proxyId) {
        setError("Выбери прокси.");
        setLoading(false);
        return;
      }

      const response = await startEmulation({
        duration_minutes: Number(duration),
        topics: payloadTopics,
        runner: "android",
        proxy_id: proxyId,
      });
      navigate(`/sessions/${response.session_id}`);
    } catch {
      setError("Не удалось запустить эмуляцию. Проверь API и логи.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card className="p-5" glow>
      <div className="mb-5 flex items-center gap-2">
        <Rocket size={16} className="text-[var(--brand)]" />
        <span className="text-sm font-semibold text-[var(--ink)]">Запуск эмуляции</span>
      </div>

      <form className="space-y-4" onSubmit={handleSubmit}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Input
            label="Длительность, минут"
            type="number"
            min={1}
            max={480}
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
            required
          />
          <Select
            label="Прокси"
            value={proxyId}
            onChange={(e) => setProxyId(e.target.value)}
          >
            {proxies.length === 0 && (
              <option value="">Нет доступных прокси</option>
            )}
            {proxies.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label} ({p.scheme}://{p.host}:{p.port})
              </option>
            ))}
          </Select>
        </div>

        <div className="space-y-2">
          <div className="text-sm font-medium text-[var(--ink-secondary)]">Темы</div>
          <div className="text-xs text-[var(--muted)]">
            Добавляй по одной теме в строку. Новая строка появляется автоматически.
          </div>
          <div className="space-y-2">
            {topics.map((topic, index) => (
              <div key={index} className="flex items-end gap-2">
                <Input
                  className="flex-1"
                  placeholder={`Тема ${index + 1}`}
                  value={topic}
                  onChange={(e) => updateTopic(index, e.target.value)}
                  required={index === 0}
                />
                {topics.length > 1 &&
                  !(index === topics.length - 1 && !topic.trim()) ? (
                  <button
                    type="button"
                    onClick={() => removeTopic(index)}
                    className="flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-lg border border-[var(--danger)]/20 bg-[var(--danger-soft)] text-[var(--danger)] transition hover:bg-[var(--danger)]/20"
                    aria-label={`Удалить тему ${index + 1}`}
                  >
                    <Trash2 size={14} />
                  </button>
                ) : null}
              </div>
            ))}
          </div>
        </div>

        {error ? (
          <div className="rounded-lg border border-[var(--danger)]/20 bg-[var(--danger-soft)] px-3 py-2 text-sm text-[var(--danger)]">
            {error}
          </div>
        ) : null}

        <Button type="submit" loading={loading} className="w-full">
          Запустить эмуляцию
        </Button>
      </form>
    </Card>
  );
}
