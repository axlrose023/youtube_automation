import { useEffect, useRef, useState } from "react";
import { Bolt, Check, ChevronDown, Clock, Trash2 } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { getProxies, startEmulation } from "@/lib/api";
import type { Proxy } from "@/types/api";

const FALLBACK_TOPICS = [
  "best forex profit",
  "quantum ai trading bot",
  "immediate earn crypto trade",
  "crypto trading signals",
  "bitcoin investment strategy",
  "forex auto trading",
];

const DURATION_OPTIONS = [15, 30, 45, 60];

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[11px] uppercase tracking-wider font-semibold mb-1.5" style={{ color: "var(--muted)" }}>
      {children}
    </div>
  );
}

export function SessionLauncher({ popularTopics }: { popularTopics?: string[] }) {
  const topicPool = popularTopics && popularTopics.length > 0 ? popularTopics : FALLBACK_TOPICS;
  const [duration, setDuration] = useState(30);
  const [durationDraft, setDurationDraft] = useState("30");
  const [topics, setTopics] = useState([""]);
  const [proxyId, setProxyId] = useState("");
  const [proxies, setProxies] = useState<Proxy[]>([]);
  const [proxyOpen, setProxyOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const popRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  const selectedProxy = proxies.find((p) => p.id === proxyId) ?? null;

  useEffect(() => {
    void getProxies(true)
      .then((data) => {
        setProxies(data.items);
        if (data.items.length > 0) setProxyId(data.items[0].id);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!proxyOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (popRef.current && !popRef.current.contains(e.target as Node)) setProxyOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") setProxyOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onEsc);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onEsc); };
  }, [proxyOpen]);

  function normalizeTopics(next: string[]) {
    const normalized = [...next];
    while (normalized.length > 1 && !normalized[normalized.length - 1]?.trim() && !normalized[normalized.length - 2]?.trim()) {
      normalized.pop();
    }
    if (normalized.length === 0) return [""];
    if (normalized.every((item) => item.trim())) normalized.push("");
    return normalized;
  }

  function updateTopic(index: number, value: string) {
    setTopics((prev) => normalizeTopics(prev.map((item, i) => (i === index ? value : item))));
  }

  function removeTopic(index: number) {
    setTopics((prev) => normalizeTopics(prev.filter((_, i) => i !== index)));
  }

  function addSuggestion(topic: string) {
    const filled = topics.filter((t) => t.trim());
    if (filled.includes(topic)) return;
    setTopics(normalizeTopics([...filled, topic, ""]));
  }

  const filledTopics = topics.filter((t) => t.trim());
  const suggestions = topicPool.filter((t) => !filledTopics.includes(t)).slice(0, 6);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const payloadTopics = topics.map((t) => t.trim()).filter(Boolean);
    if (payloadTopics.length === 0) { setError("Нужна хотя бы одна тема."); return; }
    if (!proxyId) { setError("Выбери прокси."); return; }
    setLoading(true);
    try {
      const response = await startEmulation({ duration_minutes: duration, topics: payloadTopics, runner: "android", proxy_id: proxyId });
      navigate(`/sessions/${response.session_id}`);
    } catch {
      setError("Не удалось запустить эмуляцию. Проверь API и логи.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="rounded-2xl p-5 flex flex-col gap-5" style={{ background: "var(--panel)", boxShadow: "inset 0 0 0 1px var(--line)" }}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-[15px] font-semibold leading-none" style={{ color: "var(--ink)" }}>Запустить сессию</h3>
          <p className="mt-1.5 text-[12.5px]" style={{ color: "var(--muted)" }}>Эмуляция Android · YouTube · с прокси</p>
        </div>
        <span className="inline-flex items-center gap-1 h-6 px-2 rounded-full text-[11.5px] font-medium" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>
          <span className="w-1.5 h-1.5 rounded-full" style={{ background: "var(--accent)" }} />
          Готово к запуску
        </span>
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        {/* Duration + Proxy */}
        <div className="grid grid-cols-2 gap-3">
          {/* Duration segment + custom input */}
          <div>
            <Label>Длительность</Label>
            <div className="flex items-center gap-1.5">
              <div className="flex-1 flex items-center gap-0.5 h-9 p-0.5 rounded-lg" style={{ background: "var(--panel-soft)", boxShadow: "inset 0 0 0 1px var(--line)" }}>
                {DURATION_OPTIONS.map((m) => {
                  const sel = duration === m;
                  return (
                    <button
                      key={m}
                      type="button"
                      onClick={() => { setDuration(m); setDurationDraft(String(m)); }}
                      className="flex-1 h-full rounded-md text-[12px] font-semibold tabular-nums transition-colors"
                      style={sel
                        ? { background: "var(--panel)", boxShadow: "inset 0 0 0 1px var(--line)", color: "var(--ink)" }
                        : { color: "var(--ink-secondary)" }}
                    >
                      {m}
                    </button>
                  );
                })}
              </div>
              <input
                type="number"
                min={1}
                max={1440}
                value={durationDraft}
                onChange={(e) => {
                  const raw = e.target.value;
                  setDurationDraft(raw);
                  const v = Number(raw);
                  if (raw !== "" && !Number.isNaN(v) && v > 0) setDuration(Math.min(1440, v));
                }}
                onBlur={(e) => {
                  e.currentTarget.style.boxShadow = "inset 0 0 0 1px var(--line)";
                  if (durationDraft === "" || Number(durationDraft) <= 0) {
                    setDurationDraft(String(duration));
                  } else {
                    const clamped = Math.min(1440, Math.max(1, Number(durationDraft)));
                    setDuration(clamped);
                    setDurationDraft(String(clamped));
                  }
                }}
                className="w-14 h-9 px-2 rounded-lg text-[12px] font-semibold tabular-nums text-center outline-none transition-shadow"
                style={{
                  background: DURATION_OPTIONS.includes(duration) ? "var(--panel)" : "var(--brand-soft)",
                  color: DURATION_OPTIONS.includes(duration) ? "var(--ink)" : "var(--brand-strong)",
                  boxShadow: "inset 0 0 0 1px var(--line)",
                }}
                onFocus={(e) => { e.currentTarget.style.boxShadow = "inset 0 0 0 1.5px var(--brand)"; }}
                aria-label="Своя длительность в минутах"
              />
            </div>
            <div className="mt-1 text-[11px]" style={{ color: "var(--muted)" }}>минуты · до 24ч</div>
          </div>

          {/* Proxy dropdown */}
          <div ref={popRef} className="relative">
            <Label>Прокси</Label>
            <button
              type="button"
              onClick={() => setProxyOpen((o) => !o)}
              className="w-full h-9 px-2.5 rounded-lg flex items-center gap-1.5 text-[13px] transition-colors"
              style={{ background: proxyOpen ? "var(--panel-soft)" : "var(--panel)", boxShadow: "inset 0 0 0 1px var(--line)", color: "var(--ink)" }}
            >
              <span className="flex-1 text-left truncate font-medium" style={{ color: selectedProxy ? "var(--ink)" : "var(--muted)" }}>
                {selectedProxy ? selectedProxy.label : "Нет прокси"}
              </span>
              <ChevronDown size={13} style={{ color: "var(--muted)", transform: proxyOpen ? "rotate(180deg)" : undefined, transition: "transform 0.15s" }} />
            </button>
            {selectedProxy?.country_code && (
              <div className="mt-1 text-[11px] font-mono" style={{ color: "var(--muted)" }}>{selectedProxy.country_code}</div>
            )}

            {proxyOpen && proxies.length > 0 && (
              <div className="absolute left-0 right-0 z-30 rounded-xl overflow-hidden" style={{ top: "calc(100% + 6px)", background: "var(--panel)", boxShadow: "0 8px 24px rgba(0,0,0,0.10), inset 0 0 0 1px var(--line)", maxHeight: 240, overflowY: "auto" }}>
                {proxies.map((p) => {
                  const sel = p.id === proxyId;
                  return (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => { setProxyId(p.id); setProxyOpen(false); }}
                      className="w-full h-9 px-3 flex items-center gap-2.5 text-[13px] text-left transition-colors"
                      style={{ background: sel ? "var(--panel-soft)" : undefined }}
                      onMouseEnter={(e) => { if (!sel) e.currentTarget.style.background = "var(--panel-soft)"; }}
                      onMouseLeave={(e) => { if (!sel) e.currentTarget.style.background = ""; }}
                    >
                      <span className="flex-1 truncate" style={{ color: "var(--ink)" }}>{p.label}</span>
                      <span className="font-mono text-[11px]" style={{ color: "var(--muted)" }}>{p.country_code ?? ""}</span>
                      {sel && <Check size={14} strokeWidth={2.2} style={{ color: "var(--ink-secondary)", marginLeft: 4 }} />}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* Topics */}
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <Label>Темы поиска</Label>
            <span className="text-[11px] tabular-nums" style={{ color: "var(--muted)" }}>{filledTopics.length} выбрано</span>
          </div>

          <div className="flex flex-col gap-1.5">
            {topics.map((topic, index) => {
              const isLast = index === topics.length - 1;
              const isEmpty = !topic.trim();
              return (
                <div key={index} className="flex items-center gap-1.5">
                  <input
                    className="flex-1 h-9 px-3 rounded-lg text-[13px] outline-none transition-shadow"
                    style={{
                      background: "var(--panel)",
                      boxShadow: "inset 0 0 0 1px var(--line)",
                      color: "var(--ink)",
                    }}
                    placeholder={isLast && isEmpty ? "+ новая тема" : `Тема ${index + 1}`}
                    value={topic}
                    onChange={(e) => updateTopic(index, e.target.value)}
                    onFocus={(e) => { e.currentTarget.style.boxShadow = "inset 0 0 0 1.5px var(--brand)"; }}
                    onBlur={(e) => { e.currentTarget.style.boxShadow = "inset 0 0 0 1px var(--line)"; }}
                    required={index === 0}
                  />
                  {topics.length > 1 && !(isLast && isEmpty) && (
                    <button
                      type="button"
                      onClick={() => removeTopic(index)}
                      className="h-9 w-9 shrink-0 flex items-center justify-center rounded-lg transition-colors"
                      style={{ background: "var(--danger-soft)", color: "var(--danger)", boxShadow: "inset 0 0 0 1px rgba(231,76,60,0.15)" }}
                    >
                      <Trash2 size={13} />
                    </button>
                  )}
                </div>
              );
            })}
          </div>

          {/* Popular suggestions */}
          {suggestions.length > 0 && (
            <div className="mt-2.5 flex items-baseline gap-x-2 gap-y-1.5 flex-wrap">
              <span className="text-[10.5px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>Популярные</span>
              {suggestions.map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => addSuggestion(t)}
                  className="inline-flex items-center h-[15px] px-1.5 rounded-full text-[8px] font-medium transition-colors"
                  style={{ background: "var(--panel-soft)", color: "var(--ink-secondary)" }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = "var(--brand-soft)"; e.currentTarget.style.color = "var(--brand-strong)"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = "var(--panel-soft)"; e.currentTarget.style.color = "var(--ink-secondary)"; }}
                >
                  {t}
                </button>
              ))}
            </div>
          )}
        </div>

        {error && (
          <div className="rounded-lg px-3 py-2 text-sm" style={{ background: "var(--danger-soft)", color: "var(--danger)", boxShadow: "inset 0 0 0 1px rgba(231,76,60,0.15)" }}>
            {error}
          </div>
        )}

        {/* Launch button */}
        <button
          type="submit"
          disabled={loading}
          className="h-11 flex items-center justify-center gap-2 rounded-xl text-[13.5px] font-semibold text-white transition-opacity"
          style={{ background: "var(--brand)", opacity: loading ? 0.7 : 1 }}
        >
          <Bolt size={14} />
          {loading ? "Запускаем…" : "Запустить эмуляцию"}
        </button>

        <div className="flex items-center justify-center gap-1.5 text-[11.5px] -mt-2" style={{ color: "var(--muted)" }}>
          <Clock size={11} />
          ETA: <span className="tabular-nums" style={{ color: "var(--ink-secondary)" }}>{duration} мин</span> · старт через ~5 сек
        </div>
      </form>
    </div>
  );
}
