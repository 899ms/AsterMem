/**
 * Background: API call logs page showing time/method/path/status/duration in a table;
 * row click lazily loads GET /api/logs/{id} detail expansion, supports clearing all logs.
 * Design intent: Expanded rows are inline below the table row (details row), avoiding modals
 * that interrupt browsing flow; request/response bodies displayed as JSON in pre elements.
 * A second tab shows the memory upkeep trail (write-time tidy decisions): the white-box
 * principle demands that every automatic archive is explained on screen and reversible
 * in one click, right where the user would look for "what did the system just do".
 * Key constraint: Clear is destructive and requires confirmation; status column >=400 is shown in red.
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { IconArrowBackUp, IconTrash } from "@tabler/icons-react";
import { Layout } from "../components/Layout";
import { ConfirmModal } from "../components/Modal";
import { EmptyState, LoadingLine } from "../components/EmptyState";
import { Pagination } from "../components/Pagination";
import { Tabs } from "../components/Tabs";
import { api, reportError } from "../api";
import { emitToast } from "../toast";
import { useI18n } from "../i18n";
import type { LogItem, UpkeepLogItem } from "../types";

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

/** Memory upkeep trail: what the write-time tidy pass decided, why, and one-click undo. */
function UpkeepTrail() {
  const { t } = useI18n();
  const [logs, setLogs] = useState<UpkeepLogItem[]>([]);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [restoring, setRestoring] = useState("");

  const load = useCallback(async (nextOffset: number) => {
    setLoading(true);
    try {
      const res = await api<{ logs?: UpkeepLogItem[] }>(
        "GET", `/api/arbitration/logs?limit=${PAGE_SIZE}&offset=${nextOffset}`,
      );
      setLogs(Array.isArray(res?.logs) ? res.logs : []);
      setOffset(nextOffset);
    } catch (err) {
      reportError(err, t("Unable to load logs"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load(0);
  }, [load]);

  const restore = async (memoryId: string) => {
    setRestoring(memoryId);
    try {
      await api("PUT", `/api/memories/${memoryId}`, { status: "active" });
      emitToast("success", t("Memory restored"));
      void load(offset);
    } catch (err) {
      reportError(err, t("Unable to restore memory"));
    } finally {
      setRestoring("");
    }
  };

  const actionLabel = (action: string) => {
    if (action === "supersede") return t("Replaced by newer");
    if (action === "duplicate") return t("Already known");
    return t("Kept side by side");
  };

  const titleOf = (log: UpkeepLogItem, id: string) => log.titles?.[id] || id;
  const timeOf = (value?: string) => String(value ?? "").slice(0, 19).replace("T", " ");

  return (
    <>
      <p className="muted">
        {t("When tidy-up is on, each new memory is weighed against similar older ones. Decisions land here with their reasoning; anything archived can be brought back.")}
      </p>
      {loading ? (
        <LoadingLine label={t("Loading")} />
      ) : logs.length === 0 ? (
        <EmptyState message={t("No upkeep decisions yet")} />
      ) : (
        <table className="table logs-table">
          <thead>
            <tr>
              <th>{t("Time")}</th>
              <th>{t("Decision")}</th>
              <th>{t("Memories involved")}</th>
              <th>{t("Reasoning")}</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((log) => (
              <tr key={log.id}>
                <td className="mono-sm">{timeOf(log.created_at)}</td>
                <td>{actionLabel(log.action)}</td>
                <td>
                  <div style={{ display: "grid", gap: 4 }}>
                    <span>
                      <Link to={`/view/${log.new_memory_id}`}>{titleOf(log, log.new_memory_id)}</Link>
                      {log.archived_ids.includes(log.new_memory_id) && (
                        <>
                          {" "}
                          <span className="muted mono-sm">({t("archived")})</span>{" "}
                          <button type="button" className="btn small" disabled={restoring === log.new_memory_id}
                            onClick={() => void restore(log.new_memory_id)}>
                            <IconArrowBackUp aria-hidden="true" />
                            {t("Restore")}
                          </button>
                        </>
                      )}
                    </span>
                    {log.target_ids.map((id) => (
                      <span key={id} className="muted">
                        ↔ <Link to={`/view/${id}`}>{titleOf(log, id)}</Link>
                        {log.archived_ids.includes(id) && (
                          <>
                            {" "}
                            <span className="mono-sm">({t("archived")})</span>{" "}
                            <button type="button" className="btn small" disabled={restoring === id}
                              onClick={() => void restore(id)}>
                              <IconArrowBackUp aria-hidden="true" />
                              {t("Restore")}
                            </button>
                          </>
                        )}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="muted" style={{ maxWidth: 360 }}>{log.reason || ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {!loading && (
        <Pagination offset={offset} limit={PAGE_SIZE} total={undefined} pageCount={logs.length}
          onPage={(o) => void load(o)} />
      )}
    </>
  );
}

export function LogsPage() {
  const { t } = useI18n();
  const [searchParams, setSearchParams] = useSearchParams();
  const view = searchParams.get("view") === "upkeep" ? "upkeep" : "api";
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
    if (view === "api") void load(0);
  }, [load, view]);

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
      actions={view === "api" ? (
        <button type="button" className="btn danger" onClick={() => setConfirmClear(true)}>
          <IconTrash aria-hidden="true" />
          {t("Clear logs")}
        </button>
      ) : undefined}
    >
      <Tabs
        items={[
          { key: "api", label: t("API calls") },
          { key: "upkeep", label: t("Upkeep trail") },
        ]}
        active={view}
        onChange={(key) => setSearchParams(key === "upkeep" ? { view: "upkeep" } : {})}
      />

      {view === "upkeep" ? (
        <UpkeepTrail />
      ) : loading ? (
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
      {view === "api" && !loading && (
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
