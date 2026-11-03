"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Button, Dropdown, DropdownItem, DropdownMenu, DropdownSection, DropdownTrigger } from "@heroui/react";
import { logout } from "@/lib/api/cockpit";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useTheme, type ThemeMode } from "@/lib/theme";
import { PasswordDialog } from "./PasswordDialog";

/** The three theme choices, one click each; the menu marks the active one with a check. */
const THEMES: { mode: ThemeMode; icon: string }[] = [
  { mode: "system", icon: "bi-circle-half" },
  { mode: "light", icon: "bi-sun" },
  { mode: "dark", icon: "bi-moon-stars" },
];

export function UserMenu({ username }: { username: string }) {
  const { t } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [passwordOpen, setPasswordOpen] = useState(false);
  const { mode, choose } = useTheme();

  const onAction = async (key: React.Key) => {
    if (key === "settings") router.push("/settings/");
    if (key === "password") setPasswordOpen(true);
    const theme = THEMES.find((th) => `theme-${th.mode}` === key);
    if (theme) choose(theme.mode);
    if (key === "logout") {
      await logout();
      queryClient.clear();
      router.replace("/login/");
    }
  };

  return (
    <>
      <Dropdown placement="bottom-end">
        <DropdownTrigger>
          <Button variant="light" aria-label={t("user.menu")} data-testid="user-menu" className="min-w-0 px-2" startContent={<i className="bi bi-person-circle text-lg" />}>
            <span className="hidden 2xl:inline">{username}</span>
          </Button>
        </DropdownTrigger>
        <DropdownMenu aria-label={t("user.menu")} onAction={onAction}>
          <DropdownSection title={username}>
          <DropdownItem key="settings" startContent={<i className="bi bi-sliders" />} data-testid="menu-settings">
            {t("user.settings")}
          </DropdownItem>
          <DropdownItem key="password" startContent={<i className="bi bi-key" />} data-testid="menu-password">
            {t("user.password")}
          </DropdownItem>
          <DropdownItem key="logout" color="danger" startContent={<i className="bi bi-box-arrow-right" />} data-testid="menu-logout">
            {t("user.logout")}
          </DropdownItem>
          </DropdownSection>
          <DropdownSection title={t("theme.title")}>
            {THEMES.map((th) => (
              <DropdownItem
                key={`theme-${th.mode}`}
                startContent={<i className={`bi ${th.icon}`} />}
                endContent={mode === th.mode ? <i className="bi bi-check2 text-primary" data-testid="theme-active" /> : null}
                data-testid={`theme-${th.mode}`}
              >
                {t(`theme.${th.mode}` as MessageKey)}
              </DropdownItem>
            ))}
          </DropdownSection>
        </DropdownMenu>
      </Dropdown>
      <PasswordDialog isOpen={passwordOpen} onOpenChange={setPasswordOpen} />
    </>
  );
}
