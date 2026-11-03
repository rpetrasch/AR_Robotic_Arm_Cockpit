"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Spinner } from "@heroui/react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, BackendDown } from "@/lib/api/http";
import { getMe } from "@/lib/api/cockpit";
import { StatusProvider, useStatus } from "@/lib/api/status";
import { useI18n } from "@/lib/i18n";
import { AssistantProvider, useAssistant } from "@/lib/assistant";
import { AssistantDrawer } from "./AssistantDrawer";
import { BackendDownPage } from "./BackendDownPage";
import { TopBar } from "./TopBar";

const PUBLIC = ["/login", "/setup-account"];
export const ME_KEY = ["auth", "me"] as const;

/**
 * The shell (docs/specs/spec_cockpit.md §7.1): top bar with the horizontal main menu and the live
 * status cluster, the page, and the assistant drawer on the right that survives navigation.
 * The login and first-user pages render without it.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { t } = useI18n();
  const isPublic = PUBLIC.some((p) => pathname === p || pathname.startsWith(`${p}/`));
  const me = useQuery({ queryKey: ME_KEY, queryFn: getMe, retry: false, enabled: !isPublic });
  const unauthenticated = me.error instanceof ApiError && me.error.status === 401;

  useEffect(() => {
    if (unauthenticated && !isPublic) router.replace(`/login/?next=${encodeURIComponent(pathname)}`);
  }, [unauthenticated, isPublic, pathname, router]);

  if (isPublic) return <main>{children}</main>;
  if (me.error instanceof BackendDown) return <BackendDownPage />;
  if (!me.data) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label={t("common.loading")} />
      </div>
    );
  }
  return (
    <StatusProvider enabled>
      <AssistantProvider>
        <Shell me={me.data}>{children}</Shell>
      </AssistantProvider>
    </StatusProvider>
  );
}

function Shell({ me, children }: { me: { username: string }; children: React.ReactNode }) {
  const { status, down } = useStatus();
  const { t } = useI18n();
  const assistant = useAssistant();
  if (down) return <BackendDownPage />;
  return (
    <div className="app-root">
      <TopBar username={me.username} assistantOpen={assistant.open} onAssistant={assistant.toggle} />
      {status?.fake && (
        <div className="fake-banner" role="status" data-testid="fake-banner">
          <i className="bi bi-cone-striped" /> {t("status.fake")}
        </div>
      )}
      {status?.banner && (
        <div className="bg-danger px-4 py-1 text-sm text-white" role="alert" data-testid="danger-banner">
          <i className="bi bi-exclamation-octagon" /> {status.banner}
        </div>
      )}
      <div className="app-body">
        <main className="app-main">{children}</main>
        {assistant.open && <AssistantDrawer onClose={() => assistant.setOpen(false)} />}
      </div>
    </div>
  );
}
