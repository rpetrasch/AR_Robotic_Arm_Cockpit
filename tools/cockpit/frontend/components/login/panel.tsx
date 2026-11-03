"use client";

import { PROJECT_URL } from "@/lib/project";
import { useI18n } from "@/lib/i18n";

/** The footer of the login and first-user panels: the project's repository (§7.3). */
export function ProjectLink() {
  const { t } = useI18n();
  return (
    <a href={PROJECT_URL} target="_blank" rel="noopener noreferrer" className="login-footer" data-testid="login-project">
      <i className="bi bi-git" aria-hidden /> {t("login.project")}
    </a>
  );
}
