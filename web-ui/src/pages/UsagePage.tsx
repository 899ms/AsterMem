/**
 * Background: All AI calls (explore/embedding/tagging/chunking/profile etc.) go through a unified gateway
 * recording tokens and duration; users need a place to see "how many tokens, how much money".
 * Design intent: Overview cards → daily trend (pure CSS bar chart, no chart library) → distribution by
 * scenario/model → unpriced models can be priced inline (writes to config pricing.overrides) → call detail table.
 * Key constraint: Cost is computed by the backend using current pricing; unpriced models only show tokens
 * with a pricing entry point; "estimated" flag means upstream didn't return usage—tokens are character-estimated.
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

const PAGE_SIZE = 50;

interface UsageTotals {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cached_tokens: number;
  errors: number;
  cost_usd: number;
  has_unpriced: boolean;
}

interface UsageGroup {
  caller?: string;
  kind?: string;
  day?: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number | null;
  unpriced?: boolean;
}

interface ModelRow extends UsageGroup {
  provider: string;
  provider_name: string;
  model: string;
  pricing_source: string | null;
}

interface UsageSummary {
  days: number;
  totals: UsageTotals;
  by_caller: UsageGroup[];
  by_kind: UsageGroup[];
  by_day: UsageGroup[];
  by_day_caller: UsageGroup[];
  by_model: ModelRow[];
  unpriced_models: string[];
}

interface UsageLog {
  id: number;
  timestamp: string;
  caller: string;
  kind: string;
  provider: string;
  provider_name: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cached_tokens: number;
  estimated: number;
  duration_ms: number | null;
  status: string;
  error: string | null;
  cost_usd: number | null;
}

/** Caller scene code → English copy key (then localized via t()) */
const CALLER_LABELS: Record<string, string> = {
  chat: "Chat",
  explore: "Explore",
  chunking: "Chunking",
  tagging: "Tagging",
  "meta-extract": "Metadata",
  "time-extract": "Time extraction",
  profile: "Profile",
  embedding: "Embedding",
  test: "Connection test",
  unknown: "Other",
};

const RANGE_OPTIONS = [
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
  { days: 0, label: "All time" },
];

function fmtTokens(n: number | null | undefined): string {
  const v = n ?? 0;
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 10_000) return `${(v / 1_000).toFixed(1)}k`;
  return String(v);
}

function fmtCost(v: number | null | undefined, unpriced?: boolean): string {
  if (v === null || v === undefined) return "—";
  const text = v >= 1 ? v.toFixed(2) : v.toFixed(4);
  return `${unpriced ? "≥" : ""}$${text}`;
}

/** Pie chart slice colors: theme acid green first, rest use ink-scale transition colors, stroke uniformly --ink */
const PIE_COLORS = ["#c9ff47", "#151613", "#8d9440", "#a9a698", "#5c6047", "#d8d3c0", "#6d6d66", "#b7d94e"];

function arcPath(cx: number, cy: number, r: number, startFrac: number, endFrac: number): string {
  const a0 = 2 * Math.PI * startFrac - Math.PI / 2;
  const a1 = 2 * Math.PI * endFrac - Math.PI / 2;
  const x0 = cx + r * Math.cos(a0);
  const y0 = cy + r * Math.sin(a0);
  const x1 = cx + r * Math.cos(a1);
  const y1 = cy + r * Math.sin(a1);
  const large = endFrac - startFrac > 0.5 ? 1 : 0;
  return `M ${cx} ${cy} L ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1} Z`;
}

/** Custom hover tooltip: native title appears too slowly and style is uncontrollable; use fixed positioning to follow cursor */
interface Tip {
  x: number;
  y: number;
  title: string;
  lines: string[];
}

type ShowTip = (e: { clientX: number; clientY: number }, title: string, lines: string[]) => void;

/** Pure SVG pie chart (no charting library); slices sorted by share descending, with unified coloring */
function TokenPie({ slices, showTip, hideTip }: {
  slices: { label: string; tokens: number; color: string }[];
  showTip: ShowTip;
  hideTip: () => void;
}) {
  const total = slices.reduce((sum, s) => sum + s.tokens, 0);
  if (total <= 0) return null;
  const size = 168;
  const c = size / 2;
  const r = c - 4;
  let acc = 0;
  const tipFor = (s: { label: string; tokens: number }) =>
    [`${((s.tokens / total) * 100).toFixed(1)}% · ${fmtTokens(s.tokens)} tokens`];
  return (
    <div style={{ display: "flex", gap: 20, alignItems: "center", flexWrap: "wrap" }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" onMouseLeave={hideTip}>
        {slices.length === 1 ? (
          <circle
            cx={c} cy={c} r={r}
            fill={slices[0].color} stroke="var(--ink)" strokeWidth="1.5"
            onMouseMove={(e) => showTip(e, slices[0].label, tipFor(slices[0]))}
          />
        ) : (
          slices.map((s) => {
            const start = acc / total;
            acc += s.tokens;
            const end = acc / total;
            return (
              <path
                key={s.label}
                d={arcPath(c, c, r, start, end)}
                fill={s.color}
                stroke="var(--ink)"
                strokeWidth="1.5"
                onMouseMove={(e) => showTip(e, s.label, tipFor(s))}
              />
            );
          })
        )}
      </svg>
      <div style={{ display: "grid", gap: 6 }}>
        {slices.map((s) => (
          <div key={s.label} className="mono-sm" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span
              style={{
                width: 12,
                height: 12,
                flexShrink: 0,
                background: s.color,
                border: "1px solid var(--ink)",
              }}
            />
            <span>{s.label}</span>
            <span className="muted">
              {((s.tokens / total) * 100).toFixed(1)}% · {fmtTokens(s.tokens)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function UsagePage() {
  const { t } = useI18n();
  const [days, setDays] = useState(30);
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [logs, setLogs] = useState<UsageLog[]>([]);
  const [logTotal, setLogTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [confirmClear, setConfirmClear] = useState(false);
  const [busy, setBusy] = useState(false);
  // Price override editing: model → {input, output} input field drafts
  const [priceDraft, setPriceDraft] = useState<Record<string, { input: string; output: string }>>({});

  const loadSummary = useCallback(async (range: number) => {
    setLoading(true);
    try {
      const res = await api<UsageSummary>("GET", `/api/usage/summary?days=${range}`);
      setSummary(res);
    } catch (err) {
      reportError(err, t("Unable to load usage data"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  const loadLogs = useCallback(async (nextOffset: number) => {
    try {
      const res = await api<{ logs: UsageLog[]; total: number }>(
        "GET", `/api/usage/logs?limit=${PAGE_SIZE}&offset=${nextOffset}`,
      );
      setLogs(res?.logs ?? []);
      setLogTotal(res?.total ?? 0);
      setOffset(nextOffset);
    } catch (err) {
      reportError(err, t("Unable to load usage data"));
    }
  }, [t]);

  useEffect(() => {
    void loadSummary(days);
  }, [loadSummary, days]);

  useEffect(() => {
    void loadLogs(0);
  }, [loadLogs]);

  const clearAll = async () => {
    setBusy(true);
    try {
      await api("DELETE", "/api/usage/logs");
      emitToast("success", t("Usage records cleared"));
      setConfirmClear(false);
      void loadSummary(days);
      void loadLogs(0);
    } catch (err) {
      reportError(err, t("Unable to clear usage records"));
    } finally {
      setBusy(false);
    }
  };

  const savePrice = async (model: string) => {
    const draft = priceDraft[model];
    if (!draft) return;
    try {
      await api("PUT", "/api/usage/pricing", {
        model,
        input: parseFloat(draft.input) || 0,
        output: parseFloat(draft.output) || 0,
        currency: "USD",
      });
      emitToast("success", t("Price saved"));
      setPriceDraft((prev) => {
        const next = { ...prev };
        delete next[model];
        return next;
      });
      void loadSummary(days);
    } catch (err) {
      reportError(err, t("Unable to save price"));
    }
  };

  const callerLabel = (code: string | undefined) =>
    t(CALLER_LABELS[code ?? "unknown"] ?? CALLER_LABELS.unknown);

  const totals = summary?.totals;
  const chatTokens = summary?.by_kind.find((k) => k.kind === "chat")?.total_tokens ?? 0;
  const embedTokens = summary?.by_kind.find((k) => k.kind === "embedding")?.total_tokens ?? 0;
  const maxDayTokens = Math.max(1, ...(summary?.by_day ?? []).map((d) => d.total_tokens));
  const hasEstimated = logs.some((l) => l.estimated === 1);

  // Hover tooltip (shared by stacked chart and pie chart)
  const [tip, setTip] = useState<Tip | null>(null);
  const showTip: ShowTip = (e, title, lines) => setTip({ x: e.clientX, y: e.clientY, title, lines });
  const hideTip = () => setTip(null);

  // Scene → color: assigned by token usage descending; pie chart and stacked chart share the same mapping
  const sortedCallers = [...(summary?.by_caller ?? [])]
    .filter((row) => row.total_tokens > 0)
    .sort((a, b) => b.total_tokens - a.total_tokens);
  const callerColor: Record<string, string> = {};
  sortedCallers.forEach((row, i) => {
    callerColor[row.caller ?? "unknown"] = PIE_COLORS[i % PIE_COLORS.length];
  });
  // Day → per-scene segments (stacked in global scene order, colors stable)
  const daySegments: Record<string, UsageGroup[]> = {};
  for (const row of summary?.by_day_caller ?? []) {
    if (!row.day || !row.total_tokens) continue;
    (daySegments[row.day] ??= []).push(row);
  }
  const callerRank = (c: string | undefined) => sortedCallers.findIndex((r) => r.caller === c);

  return (
    <Layout
      title={t("Usage")}
      actions={
        <button type="button" className="btn danger" onClick={() => setConfirmClear(true)}>
          <IconTrash aria-hidden="true" />
          {t("Clear usage records")}
        </button>
      }
    >
      <div className="chip-row" style={{ marginBottom: 18 }}>
        {RANGE_OPTIONS.map((opt) => (
          <button
            key={opt.days}
            type="button"
            className={`chip clickable ${days === opt.days ? "active" : ""}`}
            onClick={() => setDays(opt.days)}
          >
            {t(opt.label)}
          </button>
        ))}
      </div>

      {loading || !summary ? (
        <LoadingLine label={t("Loading")} />
      ) : totals && totals.calls === 0 ? (
        <EmptyState message={t("No AI calls recorded yet")} />
      ) : (
        <div style={{ display: "grid", gap: 18 }}>
          <section className="home-stats">
            <div className="stat-card">
              <span className="stat-value">{fmtCost(totals?.cost_usd, totals?.has_unpriced)}</span>
              <span className="stat-label">{t("Total cost")}</span>
            </div>
            <div className="stat-card">
              <span className="stat-value">{fmtTokens(totals?.total_tokens)}</span>
              <span className="stat-label">{t("Total tokens")}</span>
            </div>
            <div className="stat-card">
              <span className="stat-value">{totals?.calls ?? 0}</span>
              <span className="stat-label">{t("AI calls")}</span>
            </div>
            <div className="stat-card">
              <span className="stat-value">{fmtTokens(chatTokens)} / {fmtTokens(embedTokens)}</span>
              <span className="stat-label">{t("Chat / Embedding tokens")}</span>
            </div>
          </section>

          {totals?.has_unpriced && (
            <div className="muted small">
              {t("Some models have no pricing yet, so the total cost is a lower bound. Set prices in the pricing table below.")}
            </div>
          )}

          {(summary.by_day.length > 0) && (
            <div className="panel">
              <div className="panel-head"><span className="kicker">{t("Daily usage")}</span></div>
              <div className="panel-body">
                <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 120, overflowX: "auto" }}>
                  {summary.by_day.map((d) => {
                    const segments = [...(daySegments[d.day ?? ""] ?? [])]
                      .sort((a, b) => callerRank(a.caller) - callerRank(b.caller));
                    return (
                      <div
                        key={d.day}
                        style={{
                          flex: "1 0 14px",
                          maxWidth: 42,
                          height: "100%",
                          display: "flex",
                          flexDirection: "column",
                          justifyContent: "flex-end",
                        }}
                      >
                        {segments.map((seg) => (
                          <div
                            key={`${d.day}-${seg.caller}`}
                            onMouseMove={(e) =>
                              showTip(e, `${d.day} · ${callerLabel(seg.caller)}`, [
                                `${fmtTokens(seg.total_tokens)} tokens (${((seg.total_tokens / Math.max(1, d.total_tokens)) * 100).toFixed(0)}%)`,
                                `${t("Cost")}: ${fmtCost(seg.cost_usd, seg.unpriced)}`,
                              ])
                            }
                            onMouseLeave={hideTip}
                            style={{
                              height: `${Math.max(2, (seg.total_tokens / maxDayTokens) * 100)}%`,
                              background: callerColor[seg.caller ?? "unknown"] ?? "var(--acid)",
                              border: "1px solid var(--ink)",
                              borderBottom: "none",
                            }}
                          />
                        ))}
                        <div style={{ borderBottom: "1px solid var(--ink)" }} />
                      </div>
                    );
                  })}
                </div>
                <div className="mono-sm muted" style={{ marginTop: 8, display: "flex", justifyContent: "space-between" }}>
                  <span>{summary.by_day[0]?.day}</span>
                  <span>{summary.by_day[summary.by_day.length - 1]?.day}</span>
                </div>
              </div>
            </div>
          )}

          <div className="panel">
            <div className="panel-head"><span className="kicker">{t("By scenario")}</span></div>
            <div className="panel-body" style={{ padding: 0 }}>
              <div style={{ padding: 18, borderBottom: "1px solid var(--line)" }}>
                <TokenPie
                  slices={sortedCallers.map((row) => ({
                    label: callerLabel(row.caller),
                    tokens: row.total_tokens,
                    color: callerColor[row.caller ?? "unknown"],
                  }))}
                  showTip={showTip}
                  hideTip={hideTip}
                />
              </div>
              <table className="table">
                <thead>
                  <tr>
                    <th>{t("Scenario")}</th>
                    <th>{t("Calls")}</th>
                    <th>{t("Input tokens")}</th>
                    <th>{t("Output tokens")}</th>
                    <th>{t("Total tokens")}</th>
                    <th>{t("Cost")}</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.by_caller.map((row) => (
                    <tr key={row.caller}>
                      <td>{callerLabel(row.caller)}</td>
                      <td className="mono-sm">{row.calls}</td>
                      <td className="mono-sm">{fmtTokens(row.prompt_tokens)}</td>
                      <td className="mono-sm">{fmtTokens(row.completion_tokens)}</td>
                      <td className="mono-sm">{fmtTokens(row.total_tokens)}</td>
                      <td className="mono-sm">{fmtCost(row.cost_usd, row.unpriced)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head"><span className="kicker">{t("By model")}</span></div>
            <div className="panel-body" style={{ padding: 0 }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>{t("Model")}</th>
                    <th>{t("Provider")}</th>
                    <th>{t("Calls")}</th>
                    <th>{t("Total tokens")}</th>
                    <th>{t("Cost")}</th>
                    <th>{t("Pricing")}</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.by_model.map((row) => {
                    const key = `${row.provider}/${row.model}/${row.kind}`;
                    const draft = priceDraft[row.model];
                    return (
                      <tr key={key}>
                        <td className="mono-sm" style={{ wordBreak: "break-all" }}>{row.model || "—"}</td>
                        <td className="mono-sm">{row.provider_name || row.provider || "—"}</td>
                        <td className="mono-sm">{row.calls}</td>
                        <td className="mono-sm">{fmtTokens(row.total_tokens)}</td>
                        <td className="mono-sm">{fmtCost(row.cost_usd)}</td>
                        <td>
                          {row.pricing_source === "builtin" && <span className="chip">{t("Built-in price")}</span>}
                          {row.pricing_source === "override" && <span className="chip active">{t("Custom price")}</span>}
                          {row.pricing_source === "local" && <span className="chip">{t("Local (free)")}</span>}
                          {row.pricing_source === "openrouter" && <span className="chip">{t("OpenRouter reference")}</span>}
                          {!row.pricing_source && (
                            draft ? (
                              <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                                <input
                                  className="input mono-sm"
                                  style={{ width: 74 }}
                                  placeholder={t("Input price")}
                                  value={draft.input}
                                  onChange={(e) => setPriceDraft((p) => ({ ...p, [row.model]: { ...draft, input: e.target.value } }))}
                                />
                                <input
                                  className="input mono-sm"
                                  style={{ width: 74 }}
                                  placeholder={t("Output price")}
                                  value={draft.output}
                                  onChange={(e) => setPriceDraft((p) => ({ ...p, [row.model]: { ...draft, output: e.target.value } }))}
                                />
                                <button type="button" className="btn btn-compact" onClick={() => void savePrice(row.model)}>
                                  {t("Save")}
                                </button>
                                <span className="mono-sm muted" style={{ whiteSpace: "nowrap" }}>{t("USD / 1M tokens")}</span>
                              </span>
                            ) : (
                              <button
                                type="button"
                                className="chip clickable"
                                onClick={() => setPriceDraft((p) => ({ ...p, [row.model]: { input: "", output: "" } }))}
                              >
                                {t("Set price (USD per 1M tokens)")}
                              </button>
                            )
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="kicker">{t("Recent calls")}</span>
              {hasEstimated && <span className="mono-sm muted">{t("* estimated: upstream did not return usage")}</span>}
            </div>
            <div className="panel-body" style={{ padding: 0 }}>
              {logs.length === 0 ? (
                <div style={{ padding: 18 }}>
                  <span className="muted">{t("No AI calls recorded yet")}</span>
                </div>
              ) : (
                <table className="table">
                  <thead>
                    <tr>
                      <th>{t("Time")}</th>
                      <th>{t("Scenario")}</th>
                      <th>{t("Model")}</th>
                      <th>{t("Input tokens")}</th>
                      <th>{t("Output tokens")}</th>
                      <th>{t("Cost")}</th>
                      <th>{t("Duration")}</th>
                      <th>{t("Status")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {logs.map((log) => (
                      <tr key={log.id} title={log.error ?? undefined}>
                        <td className="mono-sm">{String(log.timestamp ?? "").slice(0, 19).replace("T", " ")}</td>
                        <td className="mono-sm">{callerLabel(log.caller)}</td>
                        <td className="mono-sm" style={{ wordBreak: "break-all" }}>{log.model || "—"}</td>
                        <td className="mono-sm">{fmtTokens(log.prompt_tokens)}{log.estimated === 1 ? "*" : ""}</td>
                        <td className="mono-sm">{fmtTokens(log.completion_tokens)}{log.estimated === 1 ? "*" : ""}</td>
                        <td className="mono-sm">{fmtCost(log.cost_usd)}</td>
                        <td className="mono-sm">{typeof log.duration_ms === "number" ? `${log.duration_ms} ms` : ""}</td>
                        <td className={`mono-sm ${log.status === "error" ? "text-danger" : "text-ok"}`}>{log.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <div style={{ padding: "0 18px 12px" }}>
                <Pagination offset={offset} limit={PAGE_SIZE} total={logTotal} pageCount={logs.length} onPage={(o) => void loadLogs(o)} />
              </div>
            </div>
          </div>
        </div>
      )}

      {tip && (
        <div
          style={{
            position: "fixed",
            left: Math.min(tip.x + 14, window.innerWidth - 220),
            top: tip.y + 14,
            zIndex: 80,
            pointerEvents: "none",
            background: "var(--ink)",
            color: "var(--paper)",
            padding: "7px 11px",
            fontSize: 12,
            lineHeight: 1.5,
            maxWidth: 260,
          }}
        >
          <div className="mono-sm" style={{ fontWeight: 600, color: "var(--acid)" }}>{tip.title}</div>
          {tip.lines.map((line, i) => (
            <div key={i} className="mono-sm">{line}</div>
          ))}
        </div>
      )}

      {confirmClear && (
        <ConfirmModal
          title={t("Clear usage records")}
          message={t("Delete all AI usage records? This cannot be undone.")}
          confirmLabel={t("Clear usage records")}
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
