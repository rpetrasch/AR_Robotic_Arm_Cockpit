"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Card, CardBody, CardHeader, Chip, Input, Tab, Tabs } from "@heroui/react";
import { ackAlert, ackAlerts, getAlertRules, getAlerts, type Alert, type Severity } from "@/lib/api/monitoring";
import { NOTIFY_KEY } from "@/lib/api/status";
import { useI18n } from "@/lib/i18n";

export const SEVERITY_COLOR: Record<Severity, "default" | "warning" | "danger" | "primary"> = {
  info: "primary",
  warn: "warning",
  error: "danger",
  critical: "danger",
};

function NotifyButton() {
  const { t } = useI18n();
  const supported = typeof window !== "undefined" && "Notification" in window;
  const [on, setOn] = useState(() => {
    try {
      return supported && localStorage.getItem(NOTIFY_KEY) === "1" && Notification.permission === "granted";
    } catch {
      return false;
    }
  });
  if (!supported) return null;
  const toggle = async () => {
    try {
      if (on) {
        localStorage.setItem(NOTIFY_KEY, "0");
        setOn(false);
        return;
      }
      const perm = Notification.permission === "default" ? await Notification.requestPermission() : Notification.permission;
      localStorage.setItem(NOTIFY_KEY, perm === "granted" ? "1" : "0");
      setOn(perm === "granted");
    } catch {
      setOn(false);
    }
  };
  return (
    <Button size="sm" variant="flat" onPress={toggle} data-testid="notify-toggle">
      <i className={`bi ${on ? "bi-bell-fill" : "bi-bell-slash"}`} /> {on ? t("alerts.notifyOn") : t("alerts.notifyOff")}
    </Button>
  );
}

/** Monitoring → Alerts (docs/specs/spec_cockpit.md §10.5): open alerts, acknowledge, history, rules. */
export function Alerts() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [view, setView] = useState("open");
  const [comment, setComment] = useState("");
  const list = useQuery({ queryKey: ["alerts", view], queryFn: () => getAlerts(view === "history" ? "resolved" : "open"), refetchInterval: 2000 });
  const rules = useQuery({ queryKey: ["alert-rules"], queryFn: getAlertRules, staleTime: Infinity });
  const done = () => qc.invalidateQueries({ queryKey: ["alerts"] });
  const ack = useMutation({ mutationFn: (a: Alert) => ackAlert(a.id, comment), onSuccess: done });
  const ackAll = useMutation({ mutationFn: (ids: number[]) => ackAlerts(ids, comment), onSuccess: done });
  const alerts = list.data?.alerts ?? [];
  const active = alerts.filter((a) => a.status === "active");

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <Tabs size="sm" selectedKey={view} onSelectionChange={(k) => setView(String(k))} aria-label={t("alerts.title")}>
          <Tab key="open" title={t("alerts.open", { n: alerts.length && view === "open" ? alerts.length : (list.data?.summary.active ?? 0) })} />
          <Tab key="history" title={t("alerts.history")} />
          <Tab key="rules" title={t("alerts.rules")} />
        </Tabs>
        {view === "open" && (
          <>
            <Input size="sm" className="max-w-xs" placeholder={t("alerts.comment")} value={comment} onValueChange={setComment} />
            <Button size="sm" variant="flat" isDisabled={!active.length} onPress={() => ackAll.mutate(active.map((a) => a.id))} data-testid="ack-all">
              {t("alerts.ackAll")}
            </Button>
          </>
        )}
        <NotifyButton />
      </div>
      {view === "rules" ? (
        <Card>
          <CardHeader className="flex-col items-start gap-1">
            <span className="font-semibold">config/cockpit/alerts.yaml</span>
            <span className="text-xs opacity-70">{t("alerts.rulesHint")}</span>
          </CardHeader>
          <CardBody className="pt-0">
            <table className="w-full text-sm" data-testid="alert-rules">
              <tbody>
                {(rules.data ?? []).map((r) => (
                  <tr key={r.id} className="border-t border-default-200">
                    <td className="py-1 pr-3 font-mono">{r.id}</td>
                    <td className="py-1 pr-3">{r.title}</td>
                    <td className="py-1 pr-3 font-mono text-xs">{r.topic ? `${r.topic}: ` : ""}{r.when}</td>
                    <td className="py-1">{r.severity}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardBody>
        </Card>
      ) : alerts.length === 0 ? (
        <p className="text-sm opacity-80" data-testid="no-alerts">
          {view === "open" ? t("alerts.none") : t("alerts.noHistory")}
        </p>
      ) : (
        <div className="flex flex-col gap-2" data-testid="alert-list">
          {alerts.map((a) => (
            <Card key={a.id} shadow="sm" data-testid={`alert-${a.rule}`} data-status={a.status}>
              <CardBody className="flex flex-row flex-wrap items-center gap-3 text-sm">
                <Chip size="sm" color={SEVERITY_COLOR[a.severity]} variant={a.status === "active" ? "solid" : "flat"}>
                  {a.severity}
                </Chip>
                <span className="font-medium">{a.message}</span>
                <span className="font-mono text-xs opacity-70">
                  {a.rule}
                  {a.subject ? ` · ${a.subject}` : ""}
                </span>
                <span className="text-xs opacity-70">
                  {t("alerts.seen", { first: new Date(a.first_seen).toLocaleTimeString(), last: new Date(a.last_seen).toLocaleTimeString(), n: a.count })}
                </span>
                {a.status === "acknowledged" && (
                  <span className="text-xs opacity-70">{t("alerts.ackedBy", { user: a.acknowledged_by, comment: a.comment ? `: ${a.comment}` : "" })}</span>
                )}
                {a.status === "resolved" && a.resolved_at && (
                  <span className="text-xs opacity-70">{t("alerts.resolved", { at: new Date(a.resolved_at).toLocaleString() })}</span>
                )}
                {a.status === "active" && (
                  <Button size="sm" variant="flat" className="ml-auto" onPress={() => ack.mutate(a)} data-testid="ack">
                    {t("alerts.ack")}
                  </Button>
                )}
              </CardBody>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
