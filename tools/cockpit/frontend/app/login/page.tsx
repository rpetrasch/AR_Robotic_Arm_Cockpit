"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Card, CardBody, CardHeader, Input } from "@heroui/react";
import { ApiError, BackendDown } from "@/lib/api/http";
import { getSetupStatus, login } from "@/lib/api/cockpit";
import { useI18n } from "@/lib/i18n";
import { LoginBackground } from "@/components/login/LoginBackground";
import { Brand } from "@/components/shell/TopBar";
import { BackendDownPage } from "@/components/shell/BackendDownPage";
import { ProjectLink } from "@/components/login/panel";
import { useDraggable } from "@/components/login/useDraggable";

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}

/** Only same-site paths are followed after sign-in, never another origin. */
function safeNext(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/environment/";
}

function LoginForm() {
  const { t } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const next = safeNext(useSearchParams().get("next"));
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const panel = useDraggable<HTMLDivElement>();
  // Also sets the csrftoken cookie the POST needs.
  const setup = useQuery({ queryKey: ["setup-account"], queryFn: getSetupStatus, retry: false });
  const submit = useMutation({
    mutationFn: () => login(username, password),
    onSuccess: () => {
      queryClient.clear();
      router.replace(next);
    },
  });

  if (setup.error instanceof BackendDown) return <BackendDownPage />;
  const err = submit.error;
  const error =
    err instanceof ApiError
      ? err.status === 429
        ? t("login.throttled", { s: err.problem.retry_after_s ?? 300 })
        : err.status === 401
          ? t("login.failed")
          : t("common.error", { detail: err.message })
      : err
        ? t("common.error", { detail: err.message })
        : null;

  return (
    <div className="login-screen" data-testid="login-page">
      <LoginBackground />
      <div className="login-panel" ref={panel} data-testid="login-panel">
        <Brand size="login" />
        <p className="text-base font-medium sm:text-lg">{t("app.tagline")}</p>
        <Card className="mt-1 w-full max-w-sm text-left shadow-lg">
          <CardHeader className="text-xl font-semibold">{t("login.title")}</CardHeader>
          <CardBody>
            {setup.data?.needed ? (
              <div className="flex flex-col gap-3" data-testid="first-run">
                {setup.data.allowed ? (
                  <>
                    <p className="text-sm">{t("login.firstRun")}</p>
                    <Button as={Link} href="/setup-account/" color="primary">
                      {t("login.firstRunLink")}
                    </Button>
                  </>
                ) : (
                  <p className="text-sm">{t("login.firstRunRemote")}</p>
                )}
              </div>
            ) : (
              <form
                className="flex flex-col gap-3"
                onSubmit={(e) => {
                  e.preventDefault();
                  submit.mutate();
                }}
              >
                <Input name="username" label={t("login.username")} value={username} onValueChange={setUsername} autoComplete="username" autoFocus isRequired />
                <Input name="password" type="password" label={t("login.password")} value={password} onValueChange={setPassword} autoComplete="current-password" isRequired />
                {error && (
                  <p className="text-sm text-danger" role="alert" data-testid="login-error">
                    {error}
                  </p>
                )}
                <Button color="primary" type="submit" isLoading={submit.isPending} data-testid="login-submit">
                  {t("login.submit")}
                </Button>
              </form>
            )}
          </CardBody>
        </Card>
        <ProjectLink />
      </div>
    </div>
  );
}
