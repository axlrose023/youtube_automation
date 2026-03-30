import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Tv2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth-context";

export function LoginPage() {
  const navigate = useNavigate();
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setLoading(true);

    try {
      await login(username, password);
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setError("Неверный логин или пароль.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="relative flex min-h-screen items-center justify-center px-4 py-8 sm:px-6 sm:py-10">
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -left-1/4 -top-1/4 h-[600px] w-[600px] rounded-full bg-[var(--brand)] opacity-[0.06] blur-[120px]" />
        <div className="absolute -bottom-1/4 -right-1/4 h-[600px] w-[600px] rounded-full bg-[var(--accent)] opacity-[0.04] blur-[120px]" />
      </div>

      <div className="relative grid w-full max-w-5xl gap-8 lg:grid-cols-[1fr_400px]">
        <div className="hidden flex-col justify-between rounded-xl border border-[var(--line)] bg-[var(--panel)] p-10 backdrop-blur-xl lg:flex">
          <div>
            <div className="flex items-center gap-2.5">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--brand)] text-white shadow-[0_4px_14px_rgba(108,92,231,0.25)]">
                <Tv2 size={18} />
              </div>
              <span className="text-sm font-semibold text-[var(--ink)]">YouTube Emulator</span>
            </div>
            <h1 className="mt-8 max-w-xl text-4xl font-semibold leading-tight text-[var(--ink)]">
              История эмуляций, реклама и управление рантаймом в одном интерфейсе.
            </h1>
            <p className="mt-4 max-w-lg text-base leading-relaxed text-[var(--muted)]">
              Фронтенд напрямую использует ваш текущий Python API. Сессии, записи рекламы,
              повторные запуски, продолжение и управление пользователями остаются на бэкенде.
            </p>
          </div>

          <div className="mt-10 grid gap-3 md:grid-cols-3">
            {[
              { label: "Сессии", value: "История" },
              { label: "Реклама", value: "Записи" },
              { label: "Рантайм", value: "Синхронизация деталки" },
            ].map((item) => (
              <div key={item.label} className="rounded-lg border border-[var(--line)] bg-[var(--bg-soft)] p-4">
                <div className="text-xs font-medium uppercase tracking-wider text-[var(--muted)]">{item.label}</div>
                <div className="mt-2 text-lg font-semibold text-[var(--ink)]">{item.value}</div>
              </div>
            ))}
          </div>
        </div>

        <Card className="p-6 sm:p-8" glow>
          <div className="mb-6 flex items-center gap-2.5 lg:hidden">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[var(--brand)] text-white">
              <Tv2 size={16} />
            </div>
            <span className="text-sm font-semibold text-[var(--ink)]">YouTube Emulator</span>
          </div>
          <div className="mb-8">
            <div className="text-xs font-medium uppercase tracking-wider text-[var(--muted)]">Вход</div>
            <h2 className="mt-2 text-2xl font-semibold text-[var(--ink)]">С возвращением</h2>
          </div>
          <form className="space-y-5" onSubmit={handleSubmit}>
            <Input
              label="Логин"
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
            <Input
              label="Пароль"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            {error ? (
              <div className="rounded-lg border border-[var(--danger)]/20 bg-[var(--danger-soft)] px-3 py-2 text-sm text-[var(--danger)]">
                {error}
              </div>
            ) : null}
            <Button type="submit" loading={loading} className="w-full">
              Войти
            </Button>
          </form>
        </Card>
      </div>
    </main>
  );
}
