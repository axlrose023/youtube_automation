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
      setError("Invalid username or password.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="relative flex min-h-screen items-center justify-center px-6 py-10">
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -left-1/4 -top-1/4 h-[600px] w-[600px] rounded-full bg-[var(--brand)] opacity-[0.06] blur-[120px]" />
        <div className="absolute -bottom-1/4 -right-1/4 h-[600px] w-[600px] rounded-full bg-[var(--accent)] opacity-[0.04] blur-[120px]" />
      </div>

      <div className="relative grid w-full max-w-5xl gap-8 lg:grid-cols-[1fr_400px]">
        <div className="flex flex-col justify-between rounded-xl border border-[var(--line)] bg-[var(--panel)] p-10 backdrop-blur-xl">
          <div>
            <div className="flex items-center gap-2.5">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--brand)] text-white shadow-[0_4px_14px_rgba(108,92,231,0.25)]">
                <Tv2 size={18} />
              </div>
              <span className="text-sm font-semibold text-[var(--ink)]">YouTube Ops</span>
            </div>
            <h1 className="mt-8 max-w-xl text-4xl font-semibold leading-tight text-[var(--ink)]">
              Emulation history, ads and runtime control in one surface.
            </h1>
            <p className="mt-4 max-w-lg text-base leading-relaxed text-[var(--muted)]">
              Frontend uses your existing Python API directly. Sessions, captures,
              retries, resumes and users management stay in the backend.
            </p>
          </div>

          <div className="mt-10 grid gap-3 md:grid-cols-3">
            {[
              { label: "Sessions", value: "History" },
              { label: "Ads", value: "Captures" },
              { label: "Runtime", value: "Detail sync" },
            ].map((item) => (
              <div key={item.label} className="rounded-lg border border-[var(--line)] bg-[var(--bg-soft)] p-4">
                <div className="text-xs font-medium uppercase tracking-wider text-[var(--muted)]">{item.label}</div>
                <div className="mt-2 text-lg font-semibold text-[var(--ink)]">{item.value}</div>
              </div>
            ))}
          </div>
        </div>

        <Card className="p-8" glow>
          <div className="mb-8">
            <div className="text-xs font-medium uppercase tracking-wider text-[var(--muted)]">Sign in</div>
            <h2 className="mt-2 text-2xl font-semibold text-[var(--ink)]">Welcome back</h2>
          </div>
          <form className="space-y-5" onSubmit={handleSubmit}>
            <Input
              label="Username"
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
            <Input
              label="Password"
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
              Sign in
            </Button>
          </form>
        </Card>
      </div>
    </main>
  );
}
