import { useState } from "react";
import { useNavigate } from "react-router-dom";

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
    <main className="flex min-h-screen items-center justify-center px-6 py-10">
      <div className="grid w-full max-w-6xl gap-8 lg:grid-cols-[1fr_420px]">
        <div className="panel flex min-h-[520px] flex-col justify-between overflow-hidden p-10">
          <div>
            <div className="inline-flex rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.24em] text-emerald-700">
              Operational admin
            </div>
            <h1 className="mt-6 max-w-2xl text-5xl font-semibold leading-[1.05] text-[var(--ink)]">
              Emulation history, ads and runtime control in one admin surface.
            </h1>
            <p className="mt-6 max-w-2xl text-base leading-8 text-[var(--muted)]">
              Frontend uses your existing Python API directly. Sessions, captures,
              retries, resumes and users management stay in the backend.
            </p>
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            <Card>
              <div className="text-xs uppercase tracking-[0.24em] text-[var(--muted)]">Sessions</div>
              <div className="mt-3 text-2xl font-semibold text-[var(--ink)]">History</div>
            </Card>
            <Card>
              <div className="text-xs uppercase tracking-[0.24em] text-[var(--muted)]">Ads</div>
              <div className="mt-3 text-2xl font-semibold text-[var(--ink)]">Captures</div>
            </Card>
            <Card>
              <div className="text-xs uppercase tracking-[0.24em] text-[var(--muted)]">Runtime</div>
              <div className="mt-3 text-2xl font-semibold text-[var(--ink)]">Detail sync</div>
            </Card>
          </div>
        </div>

        <Card className="p-8">
          <div className="mb-8">
            <div className="text-xs uppercase tracking-[0.24em] text-[var(--muted)]">Sign in</div>
            <h2 className="mt-2 text-3xl font-semibold text-[var(--ink)]">Welcome back</h2>
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
              <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
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
