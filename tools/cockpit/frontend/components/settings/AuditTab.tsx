"use client";

import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Button, Card, CardBody, Input, Table, TableBody, TableCell, TableColumn, TableHeader, TableRow } from "@heroui/react";
import { listAudit } from "@/lib/api/cockpit";
import { useI18n } from "@/lib/i18n";

const PAGE = 100;

/** Settings → Audit (§11.3): who did what and when, newest first. */
export function AuditTab() {
  const { t } = useI18n();
  const [action, setAction] = useState("");
  const pages = useInfiniteQuery({
    queryKey: ["audit", action],
    queryFn: ({ pageParam }) => listAudit({ limit: PAGE, offset: pageParam, action }),
    initialPageParam: 0,
    getNextPageParam: (last, all) => {
      const loaded = all.reduce((n, p) => n + p.results.length, 0);
      return loaded < last.count ? loaded : undefined;
    },
  });
  const rows = pages.data?.pages.flatMap((p) => p.results) ?? [];
  return (
    <Card>
      <CardBody className="flex flex-col gap-3">
        <Input className="max-w-xs" size="sm" label={t("audit.filter")} value={action} onValueChange={setAction} isClearable onClear={() => setAction("")} />
        <Table aria-label={t("settings.audit")} removeWrapper data-testid="audit-table">
          <TableHeader>
            <TableColumn>{t("audit.time")}</TableColumn>
            <TableColumn>{t("audit.actor")}</TableColumn>
            <TableColumn>{t("audit.action")}</TableColumn>
            <TableColumn>{t("audit.object")}</TableColumn>
            <TableColumn>{t("audit.ip")}</TableColumn>
          </TableHeader>
          <TableBody emptyContent={pages.isLoading ? t("common.loading") : "—"}>
            {rows.map((e) => (
              <TableRow key={e.id}>
                <TableCell className="whitespace-nowrap">{new Date(e.ts).toLocaleString()}</TableCell>
                <TableCell>{e.actor}</TableCell>
                <TableCell><code className="cli">{e.action}</code></TableCell>
                <TableCell>{e.obj}</TableCell>
                <TableCell>{e.ip}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {pages.hasNextPage && (
          <Button size="sm" variant="flat" onPress={() => pages.fetchNextPage()} isLoading={pages.isFetchingNextPage}>
            {t("audit.more")}
          </Button>
        )}
      </CardBody>
    </Card>
  );
}
