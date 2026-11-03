"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Button, Tooltip } from "@heroui/react";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { StatusCluster } from "./StatusCluster";
import { UserMenu } from "./UserMenu";

const MENU: { href: string; label: MessageKey; short?: MessageKey; icon: string }[] = [
  { href: "/setup/", label: "nav.setup", short: "nav.setup.short", icon: "bi-tools" },
  { href: "/environment/", label: "nav.environment", icon: "bi-hdd-stack" },
  { href: "/monitoring/", label: "nav.monitoring", icon: "bi-activity" },
  { href: "/simulations/", label: "nav.simulations", icon: "bi-diagram-3" },
];

export function Brand({ size = "header" }: { size?: "header" | "login" }) {
  const { t } = useI18n();
  return (
    <span className={`brand ${size === "login" ? "login-brand" : ""}`} aria-label={t("app.name")}>
      {/* eslint-disable-next-line @next/next/no-img-element -- static SVG, no optimisation needed */}
      <img className="mark" src="/brand/ar3-cockpit-mark.svg" alt="" />
      <span className="brand-ar3">AR3</span>
      <span className="brand-cockpit">Cockpit</span>
    </span>
  );
}

export function TopBar({
  username,
  assistantOpen,
  onAssistant,
}: {
  username: string;
  assistantOpen: boolean;
  onAssistant: () => void;
}) {
  const pathname = usePathname();
  const { t } = useI18n();
  return (
    <header className="topbar" data-testid="topbar">
      <Link href="/environment/" className="brand-link">
        <Brand />
      </Link>
      <nav className="main-menu" aria-label="Main menu">
        {MENU.map((m) => (
          <Link
            key={m.href}
            href={m.href}
            className={pathname.startsWith(m.href.slice(0, -1)) ? "active" : ""}
            data-testid={`menu-${m.href.replaceAll("/", "")}`}
            title={t(m.label)}
          >
            <i className={`bi ${m.icon}`} aria-hidden="true" />
            <span className="label">
              {m.short ? (
                <>
                  <span className="label-long">{t(m.label)}</span>
                  <span className="label-short">{t(m.short)}</span>
                </>
              ) : (
                t(m.label)
              )}
            </span>
          </Link>
        ))}
      </nav>
      <div className="flex-1" />
      <StatusCluster />
      <Tooltip content={t("assistant.toggle")}>
        <Button
          isIconOnly
          variant={assistantOpen ? "flat" : "light"}
          color={assistantOpen ? "secondary" : "default"}
          aria-label={t("assistant.toggle")}
          onPress={onAssistant}
          data-testid="assistant-toggle"
        >
          <i className="bi bi-stars text-lg" />
        </Button>
      </Tooltip>
      <UserMenu username={username} />
    </header>
  );
}
