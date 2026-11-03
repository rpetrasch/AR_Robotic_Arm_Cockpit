"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button, Input, Modal, ModalBody, ModalContent, ModalFooter, ModalHeader } from "@heroui/react";
import { changePassword } from "@/lib/api/cockpit";
import { useI18n } from "@/lib/i18n";

export function PasswordDialog({ isOpen, onOpenChange }: { isOpen: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useI18n();
  const [oldPassword, setOld] = useState("");
  const [newPassword, setNew] = useState("");
  const [repeat, setRepeat] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const save = useMutation({
    mutationFn: () => changePassword(oldPassword, newPassword),
    onSuccess: () => {
      setOld("");
      setNew("");
      setRepeat("");
      setMessage({ ok: true, text: t("password.saved") });
    },
    onError: (err: Error) => setMessage({ ok: false, text: err.message }),
  });
  const mismatch = repeat !== "" && repeat !== newPassword;

  return (
    <Modal isOpen={isOpen} onOpenChange={(open) => { setMessage(null); onOpenChange(open); }}>
      <ModalContent>
        {(close) => (
          <form onSubmit={(e) => { e.preventDefault(); if (!mismatch) save.mutate(); }}>
            <ModalHeader>{t("password.title")}</ModalHeader>
            <ModalBody>
              <Input type="password" label={t("password.old")} value={oldPassword} onValueChange={setOld} autoComplete="current-password" isRequired />
              <Input type="password" label={t("password.new")} value={newPassword} onValueChange={setNew} autoComplete="new-password" isRequired description={t("setupAccount.rules")} />
              <Input type="password" label={t("password.repeat")} value={repeat} onValueChange={setRepeat} autoComplete="new-password" isRequired isInvalid={mismatch} errorMessage={mismatch ? t("password.mismatch") : undefined} />
              {message && <p className={`text-sm ${message.ok ? "text-success" : "text-danger"}`} role="status">{message.text}</p>}
            </ModalBody>
            <ModalFooter>
              <Button variant="light" onPress={close}>{t("common.close")}</Button>
              <Button color="primary" type="submit" isLoading={save.isPending} isDisabled={mismatch}>{t("common.save")}</Button>
            </ModalFooter>
          </form>
        )}
      </ModalContent>
    </Modal>
  );
}
