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
  "bg-rose-100 text-rose-700",
  "bg-sky-100 text-sky-700",
  "bg-amber-100 text-amber-700",
  "bg-emerald-100 text-emerald-700",
  "bg-violet-100 text-violet-700",
  "bg-fuchsia-100 text-fuchsia-700",
  "bg-teal-100 text-teal-700",
  "bg-orange-100 text-orange-700",
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
      setListError("Failed to load users.");
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
      setFormError("Username and password are required.");
      return;
    }

    setCreating(true);

    try {
      await createUser({ ...form, username, password });
      setForm({ username: "", password: "", is_admin: false });
      setShowForm(false);
      showNotice("User created successfully.");
      await loadUsers();
    } catch (err) {
      setFormError(getErrorMessage(err, "Failed to create user."));
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
      showNotice(`User ${field === "is_active" ? "status" : "role"} updated.`);
      await loadUsers();
    } catch (err) {
      setActionError(getErrorMessage(err, `Failed to update ${field}.`));
    }
  }

  function openDeleteConfirm(user: User) {
    const canDelete = !user.is_admin || currentUser?.id === user.id;
    if (!canDelete) {
      setActionError("Admins cannot delete other admins.");
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
      showNotice("User deleted.");
      await loadUsers();
    } catch (err) {
      setActionError(getErrorMessage(err, "Failed to delete user."));
    }
  }

  const confirmCopy = useMemo(() => {
    if (!confirmState) {
      return null;
    }

    if (confirmState.kind === "delete") {
      return {
        title: "Delete user",
        description: `Delete "${confirmState.user.username}"? The user will be soft-deleted and hidden from the list.`,
        confirmLabel: "Delete",
        confirmTone: "danger" as const,
      };
    }

    const { field, nextValue, user } = confirmState;

    if (field === "is_active") {
      return {
        title: nextValue ? "Enable user" : "Disable user",
        description: `${nextValue ? "Enable" : "Disable"} "${user.username}"?`,
        confirmLabel: nextValue ? "Enable" : "Disable",
        confirmTone: nextValue ? ("default" as const) : ("danger" as const),
      };
    }

    return {
      title: "Promote user",
      description: `Promote "${user.username}" to admin?`,
      confirmLabel: "Promote",
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
        title="Admin only"
        description="Users management is available only for administrators."
      />
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-xs uppercase tracking-[0.24em] text-[var(--muted)]">Access</div>
          <h2 className="mt-2 text-2xl font-semibold text-[var(--ink)]">Users management</h2>
        </div>
        <Button
          className="gap-2"
          onClick={() => {
            setShowForm(!showForm);
            setFormError(null);
          }}
        >
          <Plus size={16} />
          New user
        </Button>
      </div>

      {/* Notice toast */}
      {notice ? (
        <div className="flex items-center gap-3 rounded-2xl border border-emerald-200 bg-emerald-50 px-5 py-3 text-sm text-emerald-700">
          <span className="h-2 w-2 shrink-0 rounded-full bg-emerald-500" />
          {notice}
        </div>
      ) : null}

      {/* Inline create form — slides open */}
      {showForm ? (
        <Card className="p-6">
          <div className="text-base font-semibold text-[var(--ink)]">Create new user</div>
          <form className="mt-4 flex flex-wrap items-end gap-4" onSubmit={handleCreate}>
            <div className="w-52 shrink-0">
              <Input
                label="Username"
                placeholder="Enter username"
                required
                value={form.username}
                onChange={(e) => setForm((prev) => ({ ...prev, username: e.target.value }))}
              />
            </div>
            <div className="w-52 shrink-0">
              <Input
                label="Password"
                type="password"
                placeholder="Enter password"
                required
                value={form.password}
                onChange={(e) => setForm((prev) => ({ ...prev, password: e.target.value }))}
              />
            </div>
            <label className="flex items-center gap-2 rounded-2xl border border-[var(--line)] bg-[var(--panel-soft)] px-4 py-3 text-sm text-[var(--ink)] cursor-pointer select-none">
              <input
                type="checkbox"
                checked={form.is_admin}
                onChange={(e) => setForm((prev) => ({ ...prev, is_admin: e.target.checked }))}
                className="accent-[var(--brand)]"
              />
              Admin
            </label>
            <Button type="submit" loading={creating}>
              Create
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setShowForm(false);
                setFormError(null);
              }}
            >
              Cancel
            </Button>
          </form>
          {formError ? (
            <div className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
              {formError}
            </div>
          ) : null}
        </Card>
      ) : null}

      {/* Users table */}
      <Card className="overflow-hidden p-0">
        {/* Table header bar */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="text-lg font-semibold text-[var(--ink)]">Users</div>
            {!loading && !listError ? (
              <Badge tone="neutral">{users.length}</Badge>
            ) : null}
          </div>
          {!loading && !listError && users.length > 0 ? (
            <div className="relative">
              <Search size={15} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-[var(--muted)]" />
              <input
                type="text"
                placeholder="Search..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-48 rounded-xl border border-[var(--line)] bg-white py-2 pl-9 pr-4 text-sm text-[var(--ink)] outline-none transition placeholder:text-slate-400 focus:border-[var(--brand)] focus:ring-2 focus:ring-rose-100"
              />
            </div>
          ) : null}
        </div>

        {/* Content */}
        {loading ? <Loader label="Loading users" /> : null}
        {!loading && listError ? (
          <div className="p-6 text-sm text-rose-700">{listError}</div>
        ) : null}
        {!loading && !listError ? (
          <div className="overflow-x-auto">
            {actionError ? (
              <div className="flex items-center gap-3 border-b border-rose-200 bg-rose-50 px-6 py-3 text-sm text-rose-700">
                <span className="h-2 w-2 shrink-0 rounded-full bg-rose-500" />
                {actionError}
              </div>
            ) : null}
            <table className="min-w-full text-sm">
              <thead className="bg-[var(--panel-soft)] text-left text-[var(--muted)]">
                <tr>
                  <th className="px-6 py-3 font-medium">User</th>
                  <th className="px-6 py-3 font-medium">Status</th>
                  <th className="px-6 py-3 font-medium">Role</th>
                  <th className="px-6 py-3 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-6 py-16 text-center text-sm text-[var(--muted)]">
                      {search ? "No users match your search." : "No users yet. Click \"New user\" to get started."}
                    </td>
                  </tr>
                ) : null}
                {filtered.map((user) => {
                  const isSelf = currentUser?.id === user.id;
                  const canDelete = !user.is_admin || isSelf;
                  const canToggleActive = !(isSelf && user.is_admin);
                  const initials = user.username.slice(0, 2).toUpperCase();

                  return (
                    <tr key={user.id} className="group border-t border-[var(--line)] transition-colors hover:bg-[var(--panel-soft)]/50">
                      <td className="px-6 py-3.5">
                        <div className="flex items-center gap-3">
                          <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold ${avatarColor(user.username)}`}>
                            {initials}
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="font-semibold text-[var(--ink)]">{user.username}</span>
                            {isSelf ? <Badge tone="info">you</Badge> : null}
                          </div>
                        </div>
                      </td>
                      <td className="px-6 py-3.5">
                        <Badge tone={user.is_active ? "success" : "warning"}>
                          {user.is_active ? "active" : "disabled"}
                        </Badge>
                      </td>
                      <td className="px-6 py-3.5">
                        <Badge tone={user.is_admin ? "warning" : "neutral"}>
                          {user.is_admin ? "admin" : "user"}
                        </Badge>
                      </td>
                      <td className="px-6 py-3.5">
                        <div className="flex flex-wrap justify-end gap-1.5">
                          <Button
                            variant="ghost"
                            className="gap-1.5 px-2.5 py-1.5 text-xs"
                            disabled={!canToggleActive}
                            onClick={() => openToggleConfirm(user, "is_active")}
                          >
                            {user.is_active ? <UserRoundX size={14} /> : <UserRoundCheck size={14} />}
                            {user.is_active ? "Disable" : "Enable"}
                          </Button>
                          {!user.is_admin ? (
                            <Button
                              variant="ghost"
                              className="gap-1.5 px-2.5 py-1.5 text-xs"
                              onClick={() => openToggleConfirm(user, "is_admin")}
                            >
                              <ShieldCheck size={14} />
                              Promote
                            </Button>
                          ) : null}
                          {canDelete ? (
                            <Button
                              variant="ghost"
                              className="gap-1.5 border-rose-200 px-2.5 py-1.5 text-xs text-rose-600 hover:bg-rose-50 hover:text-rose-700"
                              onClick={() => openDeleteConfirm(user)}
                            >
                              <Trash2 size={14} />
                              Delete
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
