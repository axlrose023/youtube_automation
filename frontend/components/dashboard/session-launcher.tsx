import { useState } from "react";
import { Trash2 } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { startEmulation } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export function SessionLauncher() {
  const [duration, setDuration] = useState("30");
  const [topics, setTopics] = useState([""]);
  const [profileId, setProfileId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  function normalizeTopics(next: string[]) {
    const normalized = [...next];

    while (
      normalized.length > 1
      && !normalized[normalized.length - 1]?.trim()
      && !normalized[normalized.length - 2]?.trim()
    ) {
      normalized.pop();
    }

    if (normalized.length === 0) {
      return [""];
    }

    if (normalized.every((item) => item.trim())) {
      normalized.push("");
    }

    return normalized;
  }

  function updateTopic(index: number, value: string) {
    setTopics((prev) =>
      normalizeTopics(
        prev.map((item, itemIndex) => (itemIndex === index ? value : item)),
      ),
    );
  }

  function removeTopic(index: number) {
    setTopics((prev) => normalizeTopics(prev.filter((_, itemIndex) => itemIndex !== index)));
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const payloadTopics = topics.map((item) => item.trim()).filter(Boolean);

      const normalizedProfileId = profileId.trim();
      if (!normalizedProfileId) {
        setError("AdsPower profile id is required.");
        setLoading(false);
        return;
      }
      if (!duration.trim()) {
        setError("Duration is required.");
        setLoading(false);
        return;
      }
      if (payloadTopics.length === 0) {
        setError("At least one topic is required.");
        setLoading(false);
        return;
      }

      const response = await startEmulation({
        duration_minutes: Number(duration),
        topics: payloadTopics,
        profile_id: normalizedProfileId,
      });
      navigate(`/sessions/${response.session_id}`);
    } catch (err) {
      setError("Failed to start emulation. Check API/logs and try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card className="p-6">
      <div className="mb-6">
        <div className="text-xs uppercase tracking-[0.24em] text-[var(--muted)]">Quick start</div>
        <h2 className="mt-2 text-xl font-semibold text-[var(--ink)]">Launch a new emulation</h2>
      </div>

      <form className="space-y-4" onSubmit={handleSubmit}>
        <div className="grid gap-4 md:grid-cols-2">
          <Input
            label="Duration, minutes"
            type="number"
            min={1}
            max={480}
            value={duration}
            onChange={(event) => setDuration(event.target.value)}
            required
          />
          <Input
            label="AdsPower profile id"
            placeholder="Required"
            value={profileId}
            onChange={(event) => setProfileId(event.target.value)}
            required
          />
        </div>

        <div className="space-y-3">
          <div>
            <div>
              <div className="text-sm font-medium text-[var(--ink)]">Topics</div>
              <div className="mt-1 text-xs text-[var(--muted)]">
                Add one topic per line. A new line appears automatically as you type.
              </div>
            </div>
          </div>
          <div className="space-y-3">
            {topics.map((topic, index) => (
              <div key={index} className="flex items-end gap-3">
                <Input
                  className="flex-1"
                  placeholder={`Topic ${index + 1}`}
                  value={topic}
                  onChange={(event) => updateTopic(index, event.target.value)}
                  required={index === 0}
                />
                {topics.length > 1 && !(index === topics.length - 1 && !topic.trim()) ? (
                  <button
                    type="button"
                    onClick={() => removeTopic(index)}
                    className="mt-auto inline-flex h-[50px] w-[50px] items-center justify-center rounded-2xl border border-rose-200 bg-rose-50 text-rose-600"
                    aria-label={`Remove topic ${index + 1}`}
                  >
                    <Trash2 size={16} />
                  </button>
                ) : null}
              </div>
            ))}
          </div>
        </div>

        {error ? (
          <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </div>
        ) : null}

        <Button type="submit" loading={loading} className="w-full">
          Start emulation
        </Button>
      </form>
    </Card>
  );
}
