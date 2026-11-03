"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Card, CardBody, CardHeader, Input } from "@heroui/react";
import { createFirstUser, getSetupStatus } from "@/lib/api/cockpit";
import { useI18n } from "@/lib/i18n";
import { LoginBackground } from "@/components/login/LoginBackground";
import { Brand } from "@/components/shell/TopBar";
import { CliLine } from "@/components/common/CopyButton";
import { ProjectLink } from "@/components/login/panel";
import { useDraggable } from "@/components/login/useDraggable";

/** First run (docs/specs/spec_cockpit.md §7.4): create the first user, from localhost only. */
export default function SetupAccountPage() {
  const { t } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const panel = useDraggable<HTMLDivElement>();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const status = useQuery({ queryKey: ["setup-account"], queryFn: getSetupStatus, retry: false });
  const create = useMutation({
    mutationFn: () => createFirstUser(username, password),
    onSuccess: () => {
      queryClient.clear();
      router.replace("/setup/");
    },
  });
  const mismatch = repeat !== "" && repeat !== password;

  let body: React.ReactNode;
  if (!status.data) {
    body = <p className="text-sm">{t("common.loading")}</p>;
  } else if (!status.data.needed) {
    body = (
      <div className="flex flex-col gap-3">
        <p className="text-sm">{t("setupAccount.done")}</p>
        <Button as={Link} href="/login/" color="primary">{t("setupAccount.toLogin")}</Button>
      </div>
    );
  } else if (!status.data.allowed) {
    body = (
      <div className="flex flex-col gap-2" data-testid="setup-remote">
        <p className="text-sm">{t("setupAccount.remote")}</p>
        <CliLine command="tools/cockpit/backend/.venv/bin/ar3ctl create-user <name>" />
      </div>
    );
  } else {
    body = (
      <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); if (!mismatch) create.mutate(); }}>
        <p className="text-sm">{t("setupAccount.intro")}</p>
        <Input name="username" label={t("login.username")} value={username} onValueChange={setUsername} autoComplete="username" autoFocus isRequired />
        <Input name="password" type="password" label={t("login.password")} value={password} onValueChange={setPassword} autoComplete="new-password" isRequired description={t("setupAccount.rules")} />
        <Input name="repeat" type="password" label={t("setupAccount.repeat")} value={repeat} onValueChange={setRepeat} autoComplete="new-password" isRequired isInvalid={mismatch} errorMessage={mismatch ? t("setupAccount.mismatch") : undefined} />
        {create.error && <p className="text-sm text-danger" role="alert" data-testid="setup-error">{create.error.message}</p>}
        <Button color="primary" type="submit" isLoading={create.isPending} isDisabled={mismatch} data-testid="setup-submit">
          {t("setupAccount.submit")}
        </Button>
      </form>
    );
  }

  return (
    <div className="login-screen" data-testid="setup-account-page">
      <LoginBackground />
      <div className="login-panel" ref={panel} data-testid="login-panel">
        <Brand size="login" />
        <Card className="mt-1 w-full max-w-sm text-left shadow-lg">
          <CardHeader className="text-xl font-semibold">{t("setupAccount.title")}</CardHeader>
          <CardBody>{body}</CardBody>
        </Card>
        <ProjectLink />
      </div>
    </div>
  );
}
