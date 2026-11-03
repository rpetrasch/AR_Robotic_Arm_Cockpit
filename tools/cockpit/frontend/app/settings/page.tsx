"use client";

import { Tab, Tabs } from "@heroui/react";
import { useI18n } from "@/lib/i18n";
import { UsersTab } from "@/components/settings/UsersTab";
import { AuditTab } from "@/components/settings/AuditTab";
import { AiTab } from "@/components/settings/AiTab";

/** Settings (§7.4, §11.3, §14.3): users, the audit log, the AI models and the knowledge index. */
export default function SettingsPage() {
  const { t } = useI18n();
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">{t("nav.settings")}</h1>
      <Tabs aria-label={t("nav.settings")}>
        <Tab key="users" title={t("settings.users")} data-testid="tab-users">
          <UsersTab />
        </Tab>
        <Tab key="audit" title={t("settings.audit")} data-testid="tab-audit">
          <AuditTab />
        </Tab>
        <Tab key="ai" title={t("settings.ai")} data-testid="tab-ai">
          <AiTab />
        </Tab>
      </Tabs>
    </div>
  );
}
