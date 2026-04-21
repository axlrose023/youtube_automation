import { useCallback, useEffect, useState } from "react";
import { Globe, Plus, Trash2 } from "lucide-react";

import { createProxy, deleteProxy, getProxies } from "@/lib/api";
import type { Proxy, ProxyCreate } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { EmptyState } from "@/components/ui/empty-state";
import { Loader } from "@/components/ui/loader";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";

const emptyForm: ProxyCreate = {
  label: "",
  scheme: "socks5",
  host: "",
  port: 1080,
  username: "",
  password: "",
  country_code: "",
  notes: "",
};

export function ProxiesScreen() {
  const [proxies, setProxies] = useState<Proxy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<ProxyCreate>({ ...emptyForm });
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getProxies();
      setProxies(data.items);
    } catch {
      setError("Не удалось загрузить прокси.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (!form.label.trim() || !form.host.trim()) {
      setFormError("Заполни название и хост.");
      return;
    }
    setSaving(true);
    try {
      await createProxy({
        ...form,
        username: form.username || null,
        password: form.password || null,
        country_code: form.country_code || null,
        notes: form.notes || null,
      });
      setForm({ ...emptyForm });
      setShowForm(false);
      await load();
    } catch {
      setFormError("Не удалось создать прокси.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!deleteId) return;
    try {
      await deleteProxy(deleteId);
      setDeleteId(null);
      await load();
    } catch {
      setError("Не удалось удалить прокси.");
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-[var(--ink)]">Прокси</h2>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Управление прокси-серверами для эмуляций
          </p>
        </div>
        <Button onClick={() => setShowForm((v) => !v)}>
          <Plus size={15} className="mr-1.5" />
          {showForm ? "Скрыть" : "Добавить"}
        </Button>
      </div>

      {showForm && (
        <Card className="p-5" glow>
          <form className="space-y-4" onSubmit={handleCreate}>
            <div className="grid gap-3 sm:grid-cols-2">
              <Input
                label="Название"
                placeholder="My proxy"
                value={form.label}
                onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))}
                required
              />
              <Select
                label="Схема"
                value={form.scheme}
                onChange={(e) => setForm((f) => ({ ...f, scheme: e.target.value }))}
              >
                <option value="socks5">socks5</option>
                <option value="socks5h">socks5h</option>
                <option value="http">http</option>
                <option value="https">https</option>
              </Select>
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              <Input
                label="Хост"
                placeholder="1.2.3.4"
                value={form.host}
                onChange={(e) => setForm((f) => ({ ...f, host: e.target.value }))}
                required
              />
              <Input
                label="Порт"
                type="number"
                min={1}
                max={65535}
                value={String(form.port)}
                onChange={(e) => setForm((f) => ({ ...f, port: Number(e.target.value) }))}
                required
              />
              <Input
                label="Страна"
                placeholder="HR"
                value={form.country_code ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, country_code: e.target.value }))}
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Input
                label="Логин"
                placeholder="user"
                value={form.username ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
              />
              <Input
                label="Пароль"
                placeholder="pass"
                type="password"
                value={form.password ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
              />
            </div>
            <Input
              label="Заметки"
              placeholder="Дополнительная информация"
              value={form.notes ?? ""}
              onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
            />

            {formError && (
              <div className="rounded-lg border border-[var(--danger)]/20 bg-[var(--danger-soft)] px-3 py-2 text-sm text-[var(--danger)]">
                {formError}
              </div>
            )}
            <Button type="submit" loading={saving}>
              Создать
            </Button>
          </form>
        </Card>
      )}

      {loading && <Loader label="Загрузка прокси" />}
      {!loading && error && <EmptyState title="Ошибка" description={error} />}
      {!loading && !error && proxies.length === 0 && (
        <EmptyState title="Нет прокси" description="Добавь первый прокси-сервер." />
      )}

      {!loading && !error && proxies.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-[var(--line)] bg-white">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--line)] bg-[var(--panel-soft)]">
                <th className="px-4 py-2.5 text-left font-medium text-[var(--ink-secondary)]">Название</th>
                <th className="px-4 py-2.5 text-left font-medium text-[var(--ink-secondary)]">URL</th>
                <th className="px-4 py-2.5 text-left font-medium text-[var(--ink-secondary)]">Страна</th>
                <th className="px-4 py-2.5 text-left font-medium text-[var(--ink-secondary)]">Статус</th>
                <th className="px-4 py-2.5 text-right font-medium text-[var(--ink-secondary)]" />
              </tr>
            </thead>
            <tbody>
              {proxies.map((proxy) => (
                <tr key={proxy.id} className="border-b border-[var(--line)] last:border-b-0">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <Globe size={14} className="text-[var(--brand)]" />
                      <span className="font-medium text-[var(--ink)]">{proxy.label}</span>
                    </div>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-[var(--muted)]">
                    {proxy.scheme}://{proxy.host}:{proxy.port}
                  </td>
                  <td className="px-4 py-3 text-[var(--ink-secondary)]">
                    {proxy.country_code || "—"}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
                        proxy.is_active
                          ? "bg-emerald-50 text-emerald-600"
                          : "bg-gray-100 text-gray-500"
                      }`}
                    >
                      {proxy.is_active ? "Активен" : "Выключен"}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => setDeleteId(proxy.id)}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-[var(--muted)] transition hover:bg-[var(--danger-soft)] hover:text-[var(--danger)]"
                    >
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ConfirmDialog
        open={deleteId !== null}
        title="Удалить прокси?"
        description="Прокси будет удален навсегда."
        confirmLabel="Удалить"
        onConfirm={handleDelete}
        onCancel={() => setDeleteId(null)}
      />
    </div>
  );
}
