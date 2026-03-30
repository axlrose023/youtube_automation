import { useEffect, useMemo, useRef, useState } from "react";
import { Plus, Search, ShieldCheck, Trash2, UserRoundCheck, UserRoundX } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Loader } from "@/components/ui/loader";
import { createUser, deleteUser, getUsers, updateUser } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { User } from "@/types/api";

function getErrorMessage(err: unknown, fallback: string) {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  return fallback;
}

const AVATAR_COLORS = [
  "bg-[var(--brand-soft)] text-[var(--brand)]",
  "bg-[var(--info-soft)] text-[var(--info)]",
  "bg-[var(--warning-soft)] text-[var(--warning)]",
  "bg-[var(--accent-soft)] text-[var(--accent)]",
  "bg-[var(--danger-soft)] text-[var(--danger)]",
  "bg-purple-500/10 text-purple-400",
  "bg-teal-500/10 text-teal-400",
  "bg-orange-500/10 text-orange-400",
];

function avatarColor(name: string) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}

type ConfirmState =
  | {
      kind: "toggle";
      user: User;
      field: "is_admin" | "is_active";
      nextValue: boolean;
    }
  | {
      kind: "delete";
      user: User;
    };

export function UsersScreen() {
  const { user: currentUser, logout } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [search, setSearch] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const [form, setForm] = useState({
    username: "",
    password: "",
    is_admin: false,
  });

  const noticeTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  function showNotice(msg: string) {
    setNotice(msg);
    clearTimeout(noticeTimer.current);
    noticeTimer.current = setTimeout(() => setNotice(null), 3000);
  }

  async function loadUsers() {
    setLoading(true);
    try {
      const data = await getUsers(1, 100);
      setUsers(data.items);
      setListError(null);
    } catch {
      setListError("Не удалось загрузить пользователей.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (currentUser?.is_admin) {
      void loadUsers();
    } else {
      setLoading(false);
    }
  }, [currentUser?.is_admin]);

  const filtered = useMemo(() => {
    if (!search.trim()) return users;
    const q = search.toLowerCase();
    return users.filter((u) => u.username.toLowerCase().includes(q));
  }, [users, search]);

  async function handleCreate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);

    const username = form.username.trim();
    const password = form.password.trim();

    if (!username || !password) {
      setFormError("Нужно заполнить логин и пароль.");
      return;
    }

    setCreating(true);

    try {
      await createUser({ ...form, username, password });
      setForm({ username: "", password: "", is_admin: false });
      setShowForm(false);
      showNotice("Пользователь создан.");
      await loadUsers();
    } catch (err) {
      setFormError(getErrorMessage(err, "Не удалось создать пользователя."));
    } finally {
      setCreating(false);
    }
  }

  function openToggleConfirm(user: User, field: "is_admin" | "is_active") {
    setConfirmState({
      kind: "toggle",
      user,
      field,
      nextValue: !user[field],
    });
  }

  async function handleToggle(
    user: User,
    field: "is_admin" | "is_active",
    nextValue: boolean,
  ) {
    setActionError(null);

    try {
      await updateUser(user.id, { [field]: nextValue });
      showNotice(field === "is_active" ? "Статус пользователя обновлен." : "Роль пользователя обновлена.");
      await loadUsers();
    } catch (err) {
      setActionError(getErrorMessage(err, `Не удалось обновить поле ${field}.`));
    }
  }

  function openDeleteConfirm(user: User) {
    const canDelete = !user.is_admin || currentUser?.id === user.id;
    if (!canDelete) {
      setActionError("Администраторы не могут удалять других администраторов.");
      return;
    }
    setConfirmState({ kind: "delete", user });
  }

  async function handleDelete(user: User) {
    setActionError(null);

    try {
      await deleteUser(user.id);
      if (currentUser?.id === user.id) {
        logout();
        return;
      }
      showNotice("Пользователь удален.");
      await loadUsers();
    } catch (err) {
      setActionError(getErrorMessage(err, "Не удалось удалить пользователя."));
    }
  }

  const confirmCopy = useMemo(() => {
    if (!confirmState) {
      return null;
    }

    if (confirmState.kind === "delete") {
      return {
        title: "Удалить пользователя",
        description: `Удалить "${confirmState.user.username}"? Пользователь будет мягко удален и скрыт из списка.`,
        confirmLabel: "Удалить",
        confirmTone: "danger" as const,
      };
    }

    const { field, nextValue, user } = confirmState;

    if (field === "is_active") {
      return {
        title: nextValue ? "Включить пользователя" : "Отключить пользователя",
        description: `${nextValue ? "Включить" : "Отключить"} "${user.username}"?`,
        confirmLabel: nextValue ? "Включить" : "Отключить",
        confirmTone: nextValue ? ("default" as const) : ("danger" as const),
      };
    }

    return {
      title: "Повысить пользователя",
      description: `Сделать "${user.username}" администратором?`,
      confirmLabel: "Повысить",
      confirmTone: "default" as const,
    };
  }, [confirmState]);

  async function handleConfirmAction() {
    if (!confirmState) {
      return;
    }

    setConfirming(true);
    try {
      if (confirmState.kind === "delete") {
        await handleDelete(confirmState.user);
      } else {
        await handleToggle(confirmState.user, confirmState.field, confirmState.nextValue);
      }
      setConfirmState(null);
    } finally {
      setConfirming(false);
    }
  }

  if (!currentUser?.is_admin) {
    return (
      <EmptyState
        title="Только для админа"
        description="Управление пользователями доступно только администраторам."
      />
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-[var(--ink)]">Управление пользователями</h2>
          <p className="mt-1 text-sm text-[var(--muted)]">Доступы и права</p>
        </div>
        <Button
          className="gap-1.5"
          onClick={() => {
            setShowForm(!showForm);
            setFormError(null);
          }}
        >
          <Plus size={15} />
          Новый пользователь
        </Button>
      </div>

      {notice ? (
        <div className="flex items-center gap-2.5 rounded-lg border border-[var(--accent)]/20 bg-[var(--accent-soft)] px-4 py-2.5 text-sm text-[var(--accent)]">
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--accent)]" />
          {notice}
        </div>
      ) : null}

      {showForm ? (
        <Card className="p-5">
          <div className="text-sm font-semibold text-[var(--ink)]">Создать пользователя</div>
          <form className="mt-4 grid grid-cols-1 items-end gap-3 sm:flex sm:flex-wrap" onSubmit={handleCreate}>
            <div className="w-full sm:w-48 sm:shrink-0">
              <Input
                label="Логин"
                placeholder="Введите логин"
                required
                value={form.username}
                onChange={(e) => setForm((prev) => ({ ...prev, username: e.target.value }))}
              />
            </div>
            <div className="w-full sm:w-48 sm:shrink-0">
              <Input
                label="Пароль"
                type="password"
                placeholder="Введите пароль"
                required
                value={form.password}
                onChange={(e) => setForm((prev) => ({ ...prev, password: e.target.value }))}
              />
            </div>
            <label className="flex cursor-pointer select-none items-center gap-2 rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2.5 text-sm text-[var(--ink-secondary)]">
              <input
                type="checkbox"
                checked={form.is_admin}
                onChange={(e) => setForm((prev) => ({ ...prev, is_admin: e.target.checked }))}
                className="accent-[var(--brand)]"
              />
              Админ
            </label>
            <Button type="submit" loading={creating}>
              Создать
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setShowForm(false);
                setFormError(null);
              }}
            >
              Отмена
            </Button>
          </form>
          {formError ? (
            <div className="mt-3 rounded-lg border border-[var(--danger)]/20 bg-[var(--danger-soft)] px-3 py-2 text-sm text-[var(--danger)]">
              {formError}
            </div>
          ) : null}
        </Card>
      ) : null}

      <Card className="overflow-hidden p-0">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] px-5 py-4">
          <div className="flex items-center gap-2.5">
            <div className="text-sm font-semibold text-[var(--ink)]">Пользователи</div>
            {!loading && !listError ? (
              <Badge tone="neutral">{users.length}</Badge>
            ) : null}
          </div>
          {!loading && !listError && users.length > 0 ? (
            <div className="relative">
              <Search size={13} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted)]" />
              <input
                type="text"
                placeholder="Поиск..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-44 rounded-lg border border-[var(--line)] bg-[var(--panel)] py-1.5 pl-8 pr-3 text-sm text-[var(--ink)] outline-none transition placeholder:text-[var(--muted)] focus:border-[var(--brand)] focus:ring-2 focus:ring-[var(--brand-soft)]"
              />
            </div>
          ) : null}
        </div>

        {loading ? <Loader label="Загрузка пользователей" /> : null}
        {!loading && listError ? (
          <div className="p-5 text-sm text-[var(--danger)]">{listError}</div>
        ) : null}
        {!loading && !listError ? (
          <div className="overflow-x-auto">
            {actionError ? (
              <div className="flex items-center gap-2.5 border-b border-[var(--danger)]/20 bg-[var(--danger-soft)] px-5 py-2.5 text-sm text-[var(--danger)]">
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--danger)]" />
                {actionError}
              </div>
            ) : null}
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--line)] text-left text-xs text-[var(--muted)]">
                  <th className="px-5 py-3 font-medium">Пользователь</th>
                  <th className="px-5 py-3 font-medium">Статус</th>
                  <th className="px-5 py-3 font-medium">Роль</th>
                  <th className="px-5 py-3 font-medium text-right">Действия</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-5 py-14 text-center text-sm text-[var(--muted)]">
                      {search ? "По вашему запросу ничего не найдено." : "Пользователей пока нет. Нажми «Новый пользователь», чтобы начать."}
                    </td>
                  </tr>
                ) : null}
                {filtered.map((user) => {
                  const isSelf = currentUser?.id === user.id;
                  const canDelete = !user.is_admin || isSelf;
                  const canToggleActive = !(isSelf && user.is_admin);
                  const initials = user.username.slice(0, 2).toUpperCase();

                  return (
                    <tr key={user.id} className="group border-t border-[var(--line)] transition-colors hover:bg-[var(--panel-hover)]">
                      <td className="px-5 py-3">
                        <div className="flex items-center gap-2.5">
                          <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-xs font-bold ${avatarColor(user.username)}`}>
                            {initials}
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="font-medium text-[var(--ink)]">{user.username}</span>
                            {isSelf ? <Badge tone="info">вы</Badge> : null}
                          </div>
                        </div>
                      </td>
                      <td className="px-5 py-3">
                        <Badge tone={user.is_active ? "success" : "warning"}>
                          {user.is_active ? "активен" : "отключен"}
                        </Badge>
                      </td>
                      <td className="px-5 py-3">
                        <Badge tone={user.is_admin ? "warning" : "neutral"}>
                          {user.is_admin ? "админ" : "пользователь"}
                        </Badge>
                      </td>
                      <td className="px-5 py-3">
                        <div className="flex flex-wrap justify-end gap-1.5">
                          <Button
                            variant="ghost"
                            className="gap-1 px-2 py-1 text-xs"
                            disabled={!canToggleActive}
                            onClick={() => openToggleConfirm(user, "is_active")}
                          >
                            {user.is_active ? <UserRoundX size={13} /> : <UserRoundCheck size={13} />}
                            {user.is_active ? "Отключить" : "Включить"}
                          </Button>
                          {!user.is_admin ? (
                            <Button
                              variant="ghost"
                              className="gap-1 px-2 py-1 text-xs"
                              onClick={() => openToggleConfirm(user, "is_admin")}
                            >
                              <ShieldCheck size={13} />
                              Повысить
                            </Button>
                          ) : null}
                          {canDelete ? (
                            <Button
                              variant="ghost"
                              className="gap-1 border-[var(--danger)]/15 px-2 py-1 text-xs text-[var(--danger)] hover:bg-[var(--danger-soft)]"
                              onClick={() => openDeleteConfirm(user)}
                            >
                              <Trash2 size={13} />
                              Удалить
                            </Button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
      </Card>
      {confirmCopy ? (
        <ConfirmDialog
          open={Boolean(confirmCopy)}
          title={confirmCopy.title}
          description={confirmCopy.description}
          confirmLabel={confirmCopy.confirmLabel}
          confirmTone={confirmCopy.confirmTone}
          loading={confirming}
          onCancel={() => {
            if (!confirming) {
              setConfirmState(null);
            }
          }}
          onConfirm={() => void handleConfirmAction()}
        />
      ) : null}
    </div>
  );
}
