/**
 * Background: API call logs page showing time/method/path/status/duration in a table;
 * row click lazily loads GET /api/logs/{id} detail expansion, supports clearing all logs.
 * Design intent: Expanded rows are inline below the table row (details row), avoiding modals
 * that interrupt browsing flow; request/response bodies displayed as JSON in pre elements.
 * Key constraint: Clear is destructive and requires confirmation; status column >=400 is shown in red.
 */
import { useCallback, useEffect, useState } from "react";
import { IconTrash } from "@tabler/icons-react";
import { Layout } from "../components/Layout";
import { ConfirmModal } from "../components/Modal";
import { EmptyState, LoadingLine } from "../components/EmptyState";
import { Pagination } from "../components/Pagination";
import { api, reportError } from "../api";
import { emitToast } from "../toast";
import { useI18n } from "../i18n";
import type { LogItem } from "../types";

const PAGE_SIZE = 50;

function pretty(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function LogsPage() {
  const { t } = useI18n();
  const [logs, setLogs] = useState<LogItem[]>([]);
  const [total, setTotal] = useState<number | undefined>(undefined);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | number | null>(null);
  const [detail, setDetail] = useState<LogItem | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (nextOffset: number) => {
    setLoading(true);
    try {
      const res = await api<unknown>("GET", `/api/logs?limit=${PAGE_SIZE}&offset=${nextOffset}`);
      const list = Array.isArray(res) ? res : (res as { logs?: unknown })?.logs;
      setLogs(Array.isArray(list) ? (list as LogItem[]) : []);
      const totalValue = (res as { total?: unknown })?.total;
      setTotal(typeof totalValue === "number" ? totalValue : undefined);
      setOffset(nextOffset);
      setExpandedId(null);
      setDetail(null);
    } catch (err) {
      reportError(err, t("Unable to load logs"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load(0);
  }, [load]);

  const toggleRow = async (log: LogItem) => {
    if (log.id === undefined) return;
    if (expandedId === log.id) {
      setExpandedId(null);
      setDetail(null);
      return;
    }
    setExpandedId(log.id);
    setDetail(null);
    try {
      const res = await api<LogItem>("GET", `/api/logs/${log.id}`);
      setDetail(res ?? log);
    } catch (err) {
      // When the detail endpoint fails, fall back to displaying the list row data without blocking interaction.
      console.error("[AsterMem] log detail failed", err);
      setDetail(log);
    }
  };

  const clearAll = async () => {
    setBusy(true);
    try {
      await api("DELETE", "/api/logs");
      emitToast("success", t("Logs cleared"));
      setConfirmClear(false);
      void load(0);
    } catch (err) {
      reportError(err, t("Unable to clear logs"));
    } finally {
      setBusy(false);
    }
  };

  const statusOf = (log: LogItem) => log.status ?? log.status_code;
  const timeOf = (log: LogItem) => String(log.created_at ?? log.timestamp ?? "").slice(0, 19).replace("T", " ");

  return (
    <Layout
      title={t("Logs")}
      actions={
        <button type="button" className="btn danger" onClick={() => setConfirmClear(true)}>
          <IconTrash aria-hidden="true" />
          {t("Clear logs")}
        </button>
      }
    >
      {loading ? (
        <LoadingLine label={t("Loading")} />
      ) : logs.length === 0 ? (
        <EmptyState message={t("No API calls recorded yet")} />
      ) : (
        <table className="table logs-table">
          <thead>
            <tr>
              <th>{t("Time")}</th>
              <th>{t("Method")}</th>
              <th>{t("Path")}</th>
              <th>{t("Status")}</th>
              <th>{t("Duration")}</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((log, i) => {
              const status = statusOf(log);
              const isOpen = expandedId === log.id;
              return [
                <tr key={`row-${log.id ?? i}`} className="clickable" onClick={() => toggleRow(log)}>
                  <td className="mono-sm">{timeOf(log)}</td>
                  <td className="mono-sm">{log.method ?? ""}</td>
                  <td className="mono-sm" style={{ wordBreak: "break-all" }}>{log.path ?? ""}</td>
                  <td className={`mono-sm ${typeof status === "number" && status >= 400 ? "text-danger" : "text-ok"}`}>
                    {status ?? ""}
                  </td>
                  <td className="mono-sm">{typeof log.duration_ms === "number" ? `${log.duration_ms} ms` : ""}</td>
                </tr>,
                isOpen ? (
                  <tr key={`detail-${log.id ?? i}`}>
                    <td colSpan={5}>
                      {!detail ? (
                        <span className="mono-sm muted">{t("Loading")}</span>
                      ) : (
                        <div style={{ display: "grid", gap: 10 }}>
                          {detail.request_body !== undefined && detail.request_body !== null && (
                            <div>
                              <span className="kicker">{t("Request body")}</span>
                              <pre className="log-detail-pre">{pretty(detail.request_body)}</pre>
                            </div>
                          )}
                          {detail.response_body !== undefined && detail.response_body !== null && (
                            <div>
                              <span className="kicker">{t("Response body")}</span>
                              <pre className="log-detail-pre">{pretty(detail.response_body)}</pre>
                            </div>
                          )}
                          <pre className="log-detail-pre">{pretty(
                            Object.fromEntries(Object.entries(detail).filter(([k]) => !["request_body", "response_body"].includes(k))),
                          )}</pre>
                        </div>
                      )}
                    </td>
                  </tr>
                ) : null,
              ];
            })}
          </tbody>
        </table>
      )}
      {!loading && (
        <Pagination offset={offset} limit={PAGE_SIZE} total={total} pageCount={logs.length} onPage={(o) => void load(o)} />
      )}

      {confirmClear && (
        <ConfirmModal
          title={t("Clear logs")}
          message={t("Delete all API call logs? This cannot be undone.")}
          confirmLabel={t("Clear logs")}
          cancelLabel={t("Cancel")}
          danger
          busy={busy}
          onConfirm={clearAll}
          onCancel={() => setConfirmClear(false)}
        />
      )}
    </Layout>
  );
}
