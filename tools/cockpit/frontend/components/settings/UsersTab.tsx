"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Card,
  CardBody,
  Input,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
  Table,
  TableBody,
  TableCell,
  TableColumn,
  TableHeader,
  TableRow,
  Tooltip,
} from "@heroui/react";
import { createUser, deleteUser, listUsers, resetPassword, type User } from "@/lib/api/cockpit";
import { useI18n } from "@/lib/i18n";
import { ME_KEY } from "@/components/shell/AppShell";

const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString() : "");

/** Settings → Users (§7.4): list, create, delete (not yourself, not the last), reset password. */
export function UsersTab() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const me = qc.getQueryData<{ id: number }>(ME_KEY);
  const users = useQuery({ queryKey: ["users"], queryFn: listUsers });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [resetFor, setResetFor] = useState<User | null>(null);
  const [deleteFor, setDeleteFor] = useState<User | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["users"] });

  const add = useMutation({
    mutationFn: () => createUser(username, password),
    onSuccess: () => {
      setUsername("");
      setPassword("");
      refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (u: User) => deleteUser(u.id),
    onSuccess: () => {
      setDeleteFor(null);
      refresh();
    },
  });

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardBody>
          <Table aria-label={t("settings.users")} removeWrapper data-testid="users-table">
            <TableHeader>
              <TableColumn>{t("users.username")}</TableColumn>
              <TableColumn>{t("users.created")}</TableColumn>
              <TableColumn>{t("users.lastLogin")}</TableColumn>
              <TableColumn align="end"> </TableColumn>
            </TableHeader>
            <TableBody emptyContent={t("common.loading")}>
              {(users.data ?? []).map((u) => {
                const self = u.id === me?.id;
                const last = (users.data?.length ?? 0) <= 1;
                return (
                  <TableRow key={u.id} data-testid={`user-${u.username}`}>
                    <TableCell>
                      {u.username}
                      {self && <span className="ml-2 text-xs opacity-60">({t("users.you")})</span>}
                    </TableCell>
                    <TableCell>{when(u.date_joined)}</TableCell>
                    <TableCell>{when(u.last_login) || t("users.never")}</TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-1">
                        <Tooltip content={t("users.reset")}>
                          <Button isIconOnly size="sm" variant="light" aria-label={t("users.reset")} onPress={() => setResetFor(u)}>
                            <i className="bi bi-key" />
                          </Button>
                        </Tooltip>
                        <Tooltip content={t("common.delete")}>
                          <span>
                            <Button
                              isIconOnly
                              size="sm"
                              variant="light"
                              color="danger"
                              aria-label={t("common.delete")}
                              isDisabled={self || last}
                              onPress={() => setDeleteFor(u)}
                              data-testid={`delete-${u.username}`}
                            >
                              <i className="bi bi-trash" />
                            </Button>
                          </span>
                        </Tooltip>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardBody>
      </Card>

      <Card>
        <CardBody>
          <form className="flex flex-wrap items-start gap-3" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
            <Input className="max-w-xs" label={t("users.username")} value={username} onValueChange={setUsername} isRequired data-testid="new-username" />
            <Input className="max-w-xs" type="password" label={t("users.password")} value={password} onValueChange={setPassword} isRequired autoComplete="new-password" description={t("setupAccount.rules")} data-testid="new-password" />
            <Button type="submit" color="primary" className="mt-2" isLoading={add.isPending} data-testid="add-user">
              {t("users.add")}
            </Button>
          </form>
          {add.error && <p className="mt-2 text-sm text-danger" role="alert">{add.error.message}</p>}
        </CardBody>
      </Card>

      <ResetDialog user={resetFor} onClose={() => setResetFor(null)} />
      <Modal isOpen={!!deleteFor} onOpenChange={(o) => !o && setDeleteFor(null)}>
        <ModalContent>
          <ModalHeader>{t("users.deleteConfirm", { name: deleteFor?.username ?? "" })}</ModalHeader>
          <ModalBody>{remove.error && <p className="text-sm text-danger">{remove.error.message}</p>}</ModalBody>
          <ModalFooter>
            <Button variant="light" onPress={() => setDeleteFor(null)}>{t("common.cancel")}</Button>
            <Button color="danger" isLoading={remove.isPending} onPress={() => deleteFor && remove.mutate(deleteFor)} data-testid="confirm-delete">
              {t("common.delete")}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </div>
  );
}

function ResetDialog({ user, onClose }: { user: User | null; onClose: () => void }) {
  const { t } = useI18n();
  const [password, setPassword] = useState("");
  const reset = useMutation({
    mutationFn: () => resetPassword(user!.id, password),
    onSuccess: () => {
      setPassword("");
      onClose();
    },
  });
  return (
    <Modal isOpen={!!user} onOpenChange={(o) => { if (!o) { reset.reset(); onClose(); } }}>
      <ModalContent>
        <form onSubmit={(e) => { e.preventDefault(); reset.mutate(); }}>
          <ModalHeader>{t("users.resetFor", { name: user?.username ?? "" })}</ModalHeader>
          <ModalBody>
            <Input type="password" label={t("password.new")} value={password} onValueChange={setPassword} autoComplete="new-password" isRequired description={t("setupAccount.rules")} />
            {reset.error && <p className="text-sm text-danger">{reset.error.message}</p>}
          </ModalBody>
          <ModalFooter>
            <Button variant="light" onPress={onClose}>{t("common.cancel")}</Button>
            <Button color="primary" type="submit" isLoading={reset.isPending}>{t("common.save")}</Button>
          </ModalFooter>
        </form>
      </ModalContent>
    </Modal>
  );
}
