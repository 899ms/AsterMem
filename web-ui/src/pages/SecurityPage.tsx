/**
 * Scanner defence status page.
 *
 * Background: the guard refuses probes before they reach a handler, and stays silent once an
 * address is blocked so a sweep does not cost one log line per request. That made it invisible:
 * the only way to tell it apart from a guard that was switched off was reading journald over SSH.
 * Design intent: show what the guard absorbed, who is currently blocked and for how long, so the
 * owner can confirm it is working — and, when it is off, say so plainly instead of rendering zeroes
 * that look like calm.
 * Key constraint: counts are per process. Blocks are held in memory by design, so the page states
 * that rather than implying a lifetime total.
 */
import { useCallback, useEffect, useState } from "react";
import { IconRefresh } from "@tabler/icons-react";
import { Layout } from "../components/Layout";
import { LoadingLine } from "../components/EmptyState";
import { api, reportError } from "../api";
import { emitToast } from "../toast";
import { useI18n } from "../i18n";

const POLL_MS = 15000;

interface BlockedAddress {
  address: string;
  strikes: number;
  blocked_for_seconds: number;
}

interface SecurityStatus {
  enabled: boolean;
  blocked?: BlockedAddress[];
  watching?: Array<{ address: string; strikes: number }>;
  tracked_addresses?: number;
  refused_total?: number;
  rule_hits?: Record<string, number>;
  block_after_strikes?: number;
  block_ladder_seconds?: number[];
  max_tracked_addresses?: number;
  trusted_proxies?: string[];
  owner_addresses?: string[];
}

function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds % 60}s`;
  return `${seconds}s`;
}

export function SecurityPage() {
  const { t } = useI18n();
  const [status, setStatus] = useState<SecurityStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [releasing, setReleasing] = useState<string | null>(null);

  const load = useCallback(async (showSpinner = false) => {
    if (showSpinner) setLoading(true);
    try {
      setStatus(await api<SecurityStatus>("GET", "/api/security"));
    } catch (err) {
      reportError(err, t("Unable to load the protection status"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load(true);
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const release = async (address: string) => {
    setReleasing(address);
    try {
      await api("POST", "/api/security/release", { address });
      // Releasing clears the strike count, so the row leaves both tables rather than dropping into
      // the watch list: the point of the button is to give the address a clean slate.
      emitToast("success", t("{address} can reach this instance again", { address }));
      await load();
    } catch (err) {
      reportError(err, t("Unable to lift the block"));
    } finally {
      setReleasing(null);
    }
  };

  const blocked = status?.blocked ?? [];
  const watching = status?.watching ?? [];
  // Requests turned away because their address was already blocked. Kept apart from the pattern
  // counts below: these never matched a rule, and the gap between the two is what the guard saves.
  const silentRefusals = status?.rule_hits?.active_block ?? 0;
  const probeHits = Object.entries(status?.rule_hits ?? {})
    .filter(([rule]) => rule !== "active_block");

  return (
    <Layout
      title={t("Security")}
      actions={
        <button type="button" className="btn small" onClick={() => void load(true)} disabled={loading}>
          <IconRefresh aria-hidden="true" />
          {t("Refresh")}
        </button>
      }
    >
      <div style={{ display: "grid", gap: 18 }}>
        {loading && !status ? (
          <LoadingLine label={t("Loading")} />
        ) : !status?.enabled ? (
          <div className="panel">
            <div className="panel-head"><span className="kicker">{t("Scanner defence")}</span></div>
            <div className="panel-body" style={{ display: "grid", gap: 8 }}>
              <strong className="text-danger">{t("Scanner defence is switched off")}</strong>
              <span className="muted">
                {t("Probes for files this service does not have are answered by the app itself. Remove ASTERMEM_SCAN_GUARD=0 and restart to turn it back on.")}
              </span>
            </div>
          </div>
        ) : (
          <>
            <div className="panel">
              <div className="panel-head"><span className="kicker">{t("Scanner defence")}</span></div>
              <div className="panel-body">
                <div className="vector-status">
                  <div className="vector-status-grid">
                    <div>
                      <span>{t("Protection")}</span>
                      <strong className="text-ok">{t("Enabled")}</strong>
                    </div>
                    <div>
                      <span>{t("Requests refused")}</span>
                      <strong>{status.refused_total ?? 0}</strong>
                    </div>
                    <div>
                      <span>{t("Currently blocked")}</span>
                      <strong>{blocked.length}</strong>
                    </div>
                  </div>
                  <p className="vector-status-message muted">
                    {t("Counts cover the current run. Blocks are held in memory and clear when the service restarts.")}
                  </p>
                </div>
              </div>
            </div>

            <div className="panel">
              <div className="panel-head">
                <span className="kicker">{t("Blocked addresses")}</span>
                {silentRefusals > 0 && (
                  <span className="mono-sm muted">
                    {t("{count} further requests turned away in silence", { count: silentRefusals })}
                  </span>
                )}
              </div>
              <div className="panel-body" style={{ padding: 0 }}>
                {blocked.length === 0 ? (
                  <div style={{ padding: 18 }}>
                    <span className="muted">{t("No address is blocked right now")}</span>
                  </div>
                ) : (
                  <table className="table">
                    <thead>
                      <tr>
                        <th>{t("Address")}</th>
                        <th>{t("Probes")}</th>
                        <th>{t("Unblocks in")}</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {blocked.map((row) => (
                        <tr key={row.address}>
                          <td className="mono-sm">{row.address}</td>
                          <td>{row.strikes}</td>
                          <td>{formatDuration(row.blocked_for_seconds)}</td>
                          <td>
                            <button
                              type="button"
                              className="btn small"
                              onClick={() => void release(row.address)}
                              disabled={releasing === row.address}
                            >
                              {t("Lift block")}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {blocked.length > 0 && (
                  <div style={{ padding: 18, borderTop: "1px solid var(--line)" }}>
                    <span className="mono-sm muted">
                      {t("Lifting a block is not an exemption: the address starts over and is blocked again if it keeps probing. Use ASTERMEM_ALLOWED_IPS for one that should never be blocked.")}
                    </span>
                  </div>
                )}
              </div>
            </div>

            {watching.length > 0 && (
              <div className="panel">
                <div className="panel-head">
                  <span className="kicker">{t("Seen probing, not blocked")}</span>
                  <span className="mono-sm muted">
                    {t("Blocked at {count} probes", { count: status.block_after_strikes ?? 3 })}
                  </span>
                </div>
                <div className="panel-body" style={{ padding: 0 }}>
                  <table className="table">
                    <thead>
                      <tr>
                        <th>{t("Address")}</th>
                        <th>{t("Probes")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {watching.map((row) => (
                        <tr key={row.address}>
                          <td className="mono-sm">{row.address}</td>
                          <td>{row.strikes}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div className="panel">
              <div className="panel-head">
                <span className="kicker">{t("What was probed for")}</span>
              </div>
              <div className="panel-body" style={{ padding: 0 }}>
                {probeHits.length === 0 ? (
                  <div style={{ padding: 18 }}>
                    <span className="muted">{t("Nothing has probed this instance yet")}</span>
                  </div>
                ) : (
                  <table className="table">
                    <thead>
                      <tr>
                        <th>{t("Pattern")}</th>
                        <th>{t("Times")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {probeHits.map(([rule, count]) => (
                        <tr key={rule}>
                          <td className="mono-sm">{rule}</td>
                          <td>{count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>

            <div className="panel">
              <div className="panel-head"><span className="kicker">{t("Settings")}</span></div>
              <div className="panel-body" style={{ display: "grid", gap: 10 }}>
                <div>
                  <span className="muted">{t("Blocked after")}</span>{" "}
                  <strong>{t("{count} probes", { count: status.block_after_strikes ?? 3 })}</strong>
                </div>
                <div>
                  <span className="muted">{t("Block lengthens through")}</span>{" "}
                  <strong className="mono-sm">
                    {(status.block_ladder_seconds ?? []).map((s) => formatDuration(s)).join(" → ")}
                  </strong>
                </div>
                <div>
                  <span className="muted">{t("Trusted proxies")}</span>{" "}
                  <strong className="mono-sm">
                    {(status.trusted_proxies ?? []).join(", ") || t("none")}
                  </strong>
                </div>
                <div>
                  <span className="muted">{t("Never blocked")}</span>{" "}
                  <strong className="mono-sm">
                    {(status.owner_addresses ?? []).join(", ") || t("Loopback and private ranges only")}
                  </strong>
                </div>
                <span className="mono-sm muted">
                  {t("Loopback, private ranges and the reverse proxy are never blocked, so an instance cannot lock its owner out.")}
                </span>
              </div>
            </div>
          </>
        )}
      </div>
    </Layout>
  );
}
