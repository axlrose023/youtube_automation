import { useEffect, useRef, useState } from "react";
import { Bolt, Check, ChevronDown, ChevronUp, Clock, Trash2 } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { getAndroidAccounts, getProxies, startEmulation } from "@/lib/api";
import type { AndroidAccountProfile, Proxy } from "@/types/api";

/* ──────────────────────────────────────────────────────────────────────────
   Запустить сессию — редизайн (раскладка «Вертикальный стек»)

   Что изменено относительно старой версии:
   • Иерархия аккаунта: email — первичный (persona), внутренняя метка — подпись
     в раскрытом списке, AVD-имя спрятано в title-тултип строки.
   • Статус аккаунта виден точкой (ready/error/unknown) + словом в закрытом поле.
   • last_used_at показывается серым справа только в раскрытом списке.
   • Поля разложены по вертикали (Аккаунт / Прокси / Длительность) — форма дышит.
   • Длительность: быстрые чипы 15м / 30м / 1ч + stepper-поле для своего значения.
   • Сохранено: бейдж «Готово к запуску», темы + популярные чипы, фиолетовая CTA, ETA.
   ────────────────────────────────────────────────────────────────────────── */

const FALLBACK_TOPICS = [
  "best forex profit",
  "quantum ai trading bot",
  "immediate earn crypto trade",
  "crypto trading signals",
  "bitcoin investment strategy",
  "forex auto trading",
];

const DURATION_PRESETS: Array<[number, string]> = [
  [15, "15м"],
  [30, "30м"],
  [60, "1ч"],
];
const PRESET_VALUES = DURATION_PRESETS.map(([m]) => m);

function statusColor(status: string): string {
  if (status === "ready") return "var(--accent)";
  if (status === "error") return "var(--danger)";
  return "var(--muted)";
}

function statusWord(status: string): string {
  if (status === "ready") return "ready";
  if (status === "error") return "error";
  return "—";
}

/** Короткая относительная подпись «N назад» из ISO-времени. */
function lastUsedLabel(iso?: string | null): string {
  if (!iso) return "не использован";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diff = Math.max(0, Date.now() - then);
  const min = Math.floor(diff / 60000);
  if (min < 1) return "только что";
  if (min < 60) return `${min} мин назад`;
  const hrs = Math.floor(min / 60);
  if (hrs < 24) return `${hrs} ч назад`;
  const days = Math.floor(hrs / 24);
  if (days === 1) return "вчера";
  return `${days} дн назад`;
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="text-[11px] uppercase tracking-wider font-semibold mb-1.5"
      style={{ color: "var(--muted)" }}
    >
      {children}
    </div>
  );
}

export function SessionLauncher({ popularTopics }: { popularTopics?: string[] }) {
  const topicPool = popularTopics && popularTopics.length > 0 ? popularTopics : FALLBACK_TOPICS;
  const [duration, setDuration] = useState(30);
  const [durationDraft, setDurationDraft] = useState("30");
  const [topics, setTopics] = useState([""]);
  const [selectedSuggestions, setSelectedSuggestions] = useState<Set<string>>(new Set());
  const [proxyId, setProxyId] = useState("");
  const [proxies, setProxies] = useState<Proxy[]>([]);
  const [proxyOpen, setProxyOpen] = useState(false);
  const [androidAccountId, setAndroidAccountId] = useState("");
  const [androidAccounts, setAndroidAccounts] = useState<AndroidAccountProfile[]>([]);
  const [accountOpen, setAccountOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const popRef = useRef<HTMLDivElement>(null);
  const accountRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  const selectedProxy = proxies.find((p) => p.id === proxyId) ?? null;
  const selectedAccount = androidAccounts.find((a) => a.id === androidAccountId) ?? null;
  const isCustomDuration = !PRESET_VALUES.includes(duration);

  useEffect(() => {
    void getProxies(true)
      .then((data) => {
        setProxies(data.items);
        if (data.items.length > 0) setProxyId(data.items[0].id);
      })
      .catch(() => {});
    void getAndroidAccounts(true)
      .then((data) => {
        setAndroidAccounts(data.items);
        const ready = data.items.find((item) => item.status === "ready") ?? data.items[0];
        if (ready) setAndroidAccountId(ready.id);
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

  useEffect(() => {
    if (!accountOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (accountRef.current && !accountRef.current.contains(e.target as Node)) setAccountOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") setAccountOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onEsc);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onEsc); };
  }, [accountOpen]);

  function commitDuration(raw: string) {
    if (raw === "" || Number(raw) <= 0) {
      setDurationDraft(String(duration));
      return;
    }
    const clamped = Math.min(1440, Math.max(1, Number(raw)));
    setDuration(clamped);
    setDurationDraft(String(clamped));
  }

  function stepDuration(delta: number) {
    const next = Math.min(1440, Math.max(1, duration + delta));
    setDuration(next);
    setDurationDraft(String(next));
  }

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
    setTopics((prev) => normalizeTopics(prev.map((item, i) => (i === index ? value : item))));
  }

  function removeTopic(index: number) {
    setTopics((prev) => normalizeTopics(prev.filter((_, i) => i !== index)));
  }

  function toggleSuggestion(topic: string) {
    setSelectedSuggestions((prev) => {
      const next = new Set(prev);
      if (next.has(topic)) next.delete(topic);
      else next.add(topic);
      return next;
    });
  }

  const filledTopics = topics.filter((t) => t.trim());
  const suggestions = topicPool.slice(0, 6);
  const totalSelected =
    filledTopics.length +
    Array.from(selectedSuggestions).filter((t) => !filledTopics.includes(t)).length;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const merged: string[] = [];
    const seen = new Set<string>();
    for (const t of topics.map((x) => x.trim()).filter(Boolean)) {
      if (!seen.has(t)) { seen.add(t); merged.push(t); }
    }
    for (const t of selectedSuggestions) {
      if (!seen.has(t)) { seen.add(t); merged.push(t); }
    }
    if (merged.length === 0) { setError("Нужна хотя бы одна тема."); return; }
    if (androidAccounts.length > 0 && !androidAccountId) { setError("Выбери аккаунт."); return; }
    if (!proxyId) { setError("Выбери прокси."); return; }
    setLoading(true);
    try {
      const response = await startEmulation({
        duration_minutes: duration,
        topics: merged,
        runner: "android",
        proxy_id: proxyId,
        android_account_id: androidAccountId || null,
      });
      navigate(`/sessions/${response.session_id}`);
    } catch {
      setError("Не удалось запустить эмуляцию. Проверь API и логи.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      className="rounded-2xl p-5 flex flex-col gap-5"
      style={{ background: "var(--panel)", boxShadow: "inset 0 0 0 1px var(--line)" }}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-[15px] font-semibold leading-none" style={{ color: "var(--ink)" }}>
            Запустить сессию
          </h3>
          <p className="mt-1.5 text-[12.5px]" style={{ color: "var(--muted)" }}>
            Эмуляция Android · YouTube · с прокси
          </p>
        </div>
        <span
          className="inline-flex items-center gap-1.5 h-6 px-2 rounded-full text-[11.5px] font-medium whitespace-nowrap"
          style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
        >
          <span className="w-1.5 h-1.5 rounded-full" style={{ background: "var(--accent)" }} />
          Готово к запуску
        </span>
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        {/* ── Аккаунт ─────────────────────────────────────────────── */}
        <div ref={accountRef} className="relative">
          <Label>Аккаунт</Label>
          <button
            type="button"
            onClick={() => setAccountOpen((o) => !o)}
            disabled={androidAccounts.length === 0}
            className="w-full h-9 pl-2.5 pr-2 rounded-lg flex items-center gap-2 text-[13px] transition-colors"
            style={{
              background: accountOpen ? "var(--panel-soft)" : "var(--panel)",
              boxShadow: accountOpen ? "inset 0 0 0 1.5px var(--brand)" : "inset 0 0 0 1px var(--line)",
              color: "var(--ink)",
              opacity: androidAccounts.length === 0 ? 0.7 : 1,
            }}
          >
            {selectedAccount && (
              <span
                className="w-1.5 h-1.5 rounded-full shrink-0"
                style={{ background: statusColor(selectedAccount.status) }}
              />
            )}
            <span
              className="flex-1 text-left truncate font-medium"
              style={{ color: selectedAccount ? "var(--ink)" : "var(--muted)" }}
            >
              {selectedAccount
                ? selectedAccount.google_email
                : androidAccounts.length === 0
                ? "Аккаунты не настроены"
                : "Выбери аккаунт"}
            </span>
            {selectedAccount && (
              <span
                className="text-[11px] font-medium shrink-0"
                style={{ color: statusColor(selectedAccount.status) }}
              >
                {statusWord(selectedAccount.status)}
              </span>
            )}
            <ChevronDown
              size={13}
              style={{
                color: "var(--muted)",
                transform: accountOpen ? "rotate(180deg)" : undefined,
                transition: "transform 0.15s",
              }}
            />
          </button>

          {accountOpen && androidAccounts.length > 0 && (
            <div
              className="absolute left-0 right-0 z-30 rounded-xl p-1"
              style={{
                top: "calc(100% + 6px)",
                background: "var(--panel)",
                boxShadow: "0 10px 28px rgba(0,0,0,0.12), inset 0 0 0 1px var(--line)",
                maxHeight: 300,
                overflowY: "auto",
              }}
            >
              {androidAccounts.map((account) => {
                const sel = account.id === androidAccountId;
                return (
                  <button
                    key={account.id}
                    type="button"
                    onClick={() => { setAndroidAccountId(account.id); setAccountOpen(false); }}
                    title={`AVD: ${account.avd_name}`}
                    className="w-full px-2.5 py-2 flex items-center gap-2.5 text-[13px] text-left rounded-lg transition-colors"
                    style={{ background: sel ? "var(--panel-soft)" : undefined }}
                    onMouseEnter={(e) => { if (!sel) e.currentTarget.style.background = "var(--panel-soft)"; }}
                    onMouseLeave={(e) => { if (!sel) e.currentTarget.style.background = ""; }}
                  >
                    <span
                      className="w-1.5 h-1.5 rounded-full shrink-0"
                      style={{ background: statusColor(account.status) }}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate" style={{ color: "var(--ink)" }}>
                        {account.google_email}
                      </span>
                      <span
                        className="block truncate text-[11px]"
                        style={{ color: account.status === "error" ? "var(--danger)" : "var(--muted)" }}
                      >
                        {account.status === "error"
                          ? account.last_error ?? "Ошибка аккаунта"
                          : account.label}
                      </span>
                    </span>
                    <span className="text-[11px] shrink-0" style={{ color: "var(--muted)" }}>
                      {lastUsedLabel(account.last_used_at)}
                    </span>
                    {sel && (
                      <Check size={14} strokeWidth={2.2} style={{ color: "var(--ink-secondary)" }} />
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* ── Прокси ──────────────────────────────────────────────── */}
        <div ref={popRef} className="relative">
          <Label>Прокси</Label>
          <button
            type="button"
            onClick={() => setProxyOpen((o) => !o)}
            className="w-full h-9 pl-2.5 pr-2 rounded-lg flex items-center gap-2.5 text-[13px] transition-colors"
            style={{
              background: proxyOpen ? "var(--panel-soft)" : "var(--panel)",
              boxShadow: proxyOpen ? "inset 0 0 0 1.5px var(--brand)" : "inset 0 0 0 1px var(--line)",
              color: "var(--ink)",
            }}
          >
            {selectedProxy?.country_code && (
              <span
                className="shrink-0 font-mono text-[10.5px] font-semibold px-1.5 py-[3px] rounded"
                style={{
                  color: "var(--ink-secondary)",
                  background: "var(--panel-soft)",
                  boxShadow: "inset 0 0 0 1px var(--line)",
                }}
              >
                {selectedProxy.country_code}
              </span>
            )}
            <span
              className="flex-1 text-left truncate font-medium"
              style={{ color: selectedProxy ? "var(--ink)" : "var(--muted)" }}
            >
              {selectedProxy ? selectedProxy.label : "Нет прокси"}
            </span>
            {selectedProxy?.city && (
              <span className="text-[12px] shrink-0" style={{ color: "var(--muted)" }}>
                {selectedProxy.city}
              </span>
            )}
            <ChevronDown
              size={13}
              style={{
                color: "var(--muted)",
                transform: proxyOpen ? "rotate(180deg)" : undefined,
                transition: "transform 0.15s",
              }}
            />
          </button>

          {proxyOpen && proxies.length > 0 && (
            <div
              className="absolute left-0 right-0 z-30 rounded-xl p-1"
              style={{
                top: "calc(100% + 6px)",
                background: "var(--panel)",
                boxShadow: "0 10px 28px rgba(0,0,0,0.12), inset 0 0 0 1px var(--line)",
                maxHeight: 260,
                overflowY: "auto",
              }}
            >
              {proxies.map((p) => {
                const sel = p.id === proxyId;
                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => { setProxyId(p.id); setProxyOpen(false); }}
                    className="w-full px-2.5 py-2 flex items-center gap-2.5 text-[13px] text-left rounded-lg transition-colors"
                    style={{ background: sel ? "var(--panel-soft)" : undefined }}
                    onMouseEnter={(e) => { if (!sel) e.currentTarget.style.background = "var(--panel-soft)"; }}
                    onMouseLeave={(e) => { if (!sel) e.currentTarget.style.background = ""; }}
                  >
                    {p.country_code && (
                      <span
                        className="shrink-0 font-mono text-[10.5px] font-semibold px-1.5 py-[3px] rounded"
                        style={{
                          color: "var(--ink-secondary)",
                          background: "var(--panel-soft)",
                          boxShadow: "inset 0 0 0 1px var(--line)",
                        }}
                      >
                        {p.country_code}
                      </span>
                    )}
                    <span className="flex-1 truncate" style={{ color: "var(--ink)" }}>{p.label}</span>
                    {p.city && (
                      <span className="text-[12px]" style={{ color: "var(--muted)" }}>{p.city}</span>
                    )}
                    {sel && (
                      <Check size={14} strokeWidth={2.2} style={{ color: "var(--ink-secondary)" }} />
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* ── Длительность: чипы + stepper ────────────────────────── */}
        <div>
          <Label>Длительность</Label>
          <div className="flex items-center gap-1.5 flex-wrap">
            {DURATION_PRESETS.map(([m, lbl]) => {
              const sel = duration === m;
              return (
                <button
                  key={m}
                  type="button"
                  onClick={() => { setDuration(m); setDurationDraft(String(m)); }}
                  className="h-[30px] px-3 rounded-lg text-[12.5px] font-semibold tabular-nums transition-colors"
                  style={
                    sel
                      ? { background: "var(--ink)", color: "#fff", boxShadow: "inset 0 0 0 1px var(--ink)" }
                      : { background: "var(--panel-soft)", color: "var(--ink-secondary)", boxShadow: "inset 0 0 0 1px var(--line)" }
                  }
                >
                  {lbl}
                </button>
              );
            })}

            <div
              className="ml-auto flex items-center gap-1.5 h-[34px] pl-2.5 pr-1.5 rounded-lg transition-colors"
              style={{
                background: isCustomDuration ? "var(--brand-soft)" : "var(--panel)",
                boxShadow: isCustomDuration
                  ? "inset 0 0 0 1px rgba(108,92,231,0.2)"
                  : "inset 0 0 0 1px var(--line)",
              }}
            >
              <span className="text-[11px]" style={{ color: "var(--muted)" }}>своё</span>
              <input
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                value={durationDraft}
                onChange={(e) => {
                  const raw = e.target.value;
                  setDurationDraft(raw);
                  const v = Number(raw);
                  if (raw !== "" && !Number.isNaN(v) && v > 0) setDuration(Math.min(1440, v));
                }}
                onBlur={(e) => commitDuration(e.currentTarget.value)}
                className="w-8 bg-transparent outline-none text-[13px] font-semibold tabular-nums text-right"
                style={{ color: isCustomDuration ? "var(--brand-strong)" : "var(--ink)" }}
                aria-label="Своя длительность в минутах"
              />
              <span className="text-[11px]" style={{ color: "var(--muted)" }}>мин</span>
              <span className="flex flex-col">
                <button
                  type="button"
                  onClick={() => stepDuration(1)}
                  className="w-[18px] h-[14px] flex items-center justify-center rounded transition-colors"
                  style={{ color: "var(--muted)" }}
                  aria-label="Больше"
                >
                  <ChevronUp size={11} strokeWidth={3} />
                </button>
                <button
                  type="button"
                  onClick={() => stepDuration(-1)}
                  className="w-[18px] h-[14px] flex items-center justify-center rounded transition-colors"
                  style={{ color: "var(--muted)" }}
                  aria-label="Меньше"
                >
                  <ChevronDown size={11} strokeWidth={3} />
                </button>
              </span>
            </div>
          </div>
        </div>

        {/* ── Темы ────────────────────────────────────────────────── */}
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <Label>Темы поиска</Label>
            <span className="text-[11px] tabular-nums" style={{ color: "var(--muted)" }}>
              {totalSelected} выбрано
            </span>
          </div>

          <div className="flex flex-col gap-1.5">
            {topics.map((topic, index) => {
              const isLast = index === topics.length - 1;
              const isEmpty = !topic.trim();
              return (
                <div key={index} className="flex items-center gap-1.5">
                  <input
                    className="flex-1 h-9 px-3 rounded-lg text-[13px] outline-none transition-shadow"
                    style={{ background: "var(--panel)", boxShadow: "inset 0 0 0 1px var(--line)", color: "var(--ink)" }}
                    placeholder={isLast && isEmpty ? "+ новая тема" : `Тема ${index + 1}`}
                    value={topic}
                    onChange={(e) => updateTopic(index, e.target.value)}
                    onFocus={(e) => { e.currentTarget.style.boxShadow = "inset 0 0 0 1.5px var(--brand)"; }}
                    onBlur={(e) => { e.currentTarget.style.boxShadow = "inset 0 0 0 1px var(--line)"; }}
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

          {suggestions.length > 0 && (
            <div className="mt-2.5 flex items-center gap-1.5 flex-wrap">
              <span className="text-[10px] uppercase tracking-wider mr-0.5" style={{ color: "var(--muted)" }}>
                Популярные
              </span>
              {suggestions.map((t) => {
                const sel = selectedSuggestions.has(t);
                return (
                  <button
                    key={t}
                    type="button"
                    onClick={() => toggleSuggestion(t)}
                    className="inline-flex items-center h-6 px-2.5 rounded-full text-[11.5px] font-medium transition-colors"
                    style={
                      sel
                        ? { background: "var(--ink)", color: "#fff", boxShadow: "inset 0 0 0 1px var(--ink)" }
                        : { background: "transparent", color: "var(--muted)", boxShadow: "inset 0 0 0 1px var(--line)" }
                    }
                    onMouseEnter={(e) => { if (!sel) { e.currentTarget.style.color = "var(--ink-secondary)"; e.currentTarget.style.boxShadow = "inset 0 0 0 1px var(--line-strong)"; } }}
                    onMouseLeave={(e) => { if (!sel) { e.currentTarget.style.color = "var(--muted)"; e.currentTarget.style.boxShadow = "inset 0 0 0 1px var(--line)"; } }}
                  >
                    {t}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {error && (
          <div
            className="rounded-lg px-3 py-2 text-sm"
            style={{ background: "var(--danger-soft)", color: "var(--danger)", boxShadow: "inset 0 0 0 1px rgba(231,76,60,0.15)" }}
          >
            {error}
          </div>
        )}

        {/* ── CTA + ETA ───────────────────────────────────────────── */}
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
