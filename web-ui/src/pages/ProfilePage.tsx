/**
 * Background: The sole review entry for the profile layer (PRD_UserProfile v0.3). AI generates
 * claims the user never wrote based on memories; the product requires these to be always visible,
 * editable, and disableable—not a black box.
 * Design intent: Four tabs—Overview (toggle + output preview + manual trigger for fast cycle),
 * Fields (L1/L2 fields and manual profile, AI has no write access), Claims (L3 list + human
 * adjudication of pending issues), Dream (trigger suggestions, candidate version diff review, activate/discard).
 * Key constraint: Candidate versions must be diffed before activation; claim sources are clickable to jump back to the original memory.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  IconPlayerPlay, IconZzz, IconRefresh, IconCheck, IconTrash, IconX, IconSparkles,
  IconChevronDown, IconChevronRight, IconPlus,
} from "@tabler/icons-react";
import { Layout } from "../components/Layout";
import { Tabs } from "../components/Tabs";
import { EmptyState, LoadingLine } from "../components/EmptyState";
import { Select } from "../components/Select";
import { api, reportError } from "../api";
import { emitToast } from "../toast";
import { useI18n } from "../i18n";
import type {
  DreamDiff, DreamItem, ProfileClaim, ProfileFieldHistoryItem, ProfileFields,
  ProfileStatus,
} from "../types";

const TIER_LABELS: Record<string, string> = {
  core: "Long-term",
  recent: "Recent",
  map: "Topic map",
};

const PENDING_LABELS: Record<string, string> = {
  stale: "Source archived",
  orphaned: "Source deleted",
  unsupported: "Not supported by source",
  aging: "Possibly outdated",
  weakened: "Underlying claim invalid",
  conflict: "Conflicting claims",
};

/** Site-wide time format: truncate to minute, remove ISO's T */
function formatWhen(iso?: string): string {
  return iso ? iso.slice(0, 16).replace("T", " ") : "";
}

function SourceLinks({ claim }: { claim: ProfileClaim }) {
  if (!claim.sources?.length) return null;
  return (
    <span className="claim-sources mono-sm">
      {claim.sources.map((src) => {
        const id = String(src);
        return claim.source_kind === "memory" ? (
          <Link key={id} to={`/view/${id}`}>{id}</Link>
        ) : (
          <span key={id}>#{id}</span>
        );
      })}
    </span>
  );
}

export function ProfilePage() {
  const { t } = useI18n();
  const [tab, setTab] = useState("overview");
  const [status, setStatus] = useState<ProfileStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");

  // Overview
  const [previewLevel, setPreviewLevel] = useState("standard");
  const [withSources, setWithSources] = useState(false);
  const [preview, setPreview] = useState("");

  // Fields
  const [fields, setFields] = useState<ProfileFields | null>(null);
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const [fieldHistory, setFieldHistory] = useState<ProfileFieldHistoryItem[]>([]);
  const [openHistory, setOpenHistory] = useState<Record<string, boolean>>({});
  const [manual, setManual] = useState("");
  // L2 optional fields are added on demand like the Provider catalog: only show those with content by default,
  // rest listed as clickable candidates; snapshot once on load to prevent rows disappearing when user clears input
  const [shownOptional, setShownOptional] = useState<string[]>([]);

  /**
   * Background: History is a flat table sorted by time desc, but users ask "what was the previous value" next to the field.
   * Design intent: Group by field and attach below each input, preserving backend's reverse order (newest first).
   */
  const historyByField = useMemo(() => {
    const grouped: Record<string, ProfileFieldHistoryItem[]> = {};
    for (const item of fieldHistory) {
      (grouped[item.key] ??= []).push(item);
    }
    return grouped;
  }, [fieldHistory]);

  // Claims
  const [claims, setClaims] = useState<ProfileClaim[]>([]);
  const [pendingClaims, setPendingClaims] = useState<ProfileClaim[]>([]);

  // Dream
  const [dreams, setDreams] = useState<DreamItem[]>([]);
  const [dreamInstructions, setDreamInstructions] = useState("");
  const [diffs, setDiffs] = useState<Record<number, DreamDiff>>({});

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await api<ProfileStatus>("GET", "/api/profile/status"));
    } catch (err) {
      reportError(err, t("Unable to load profile status"));
    }
  }, [t]);

  const loadPreview = useCallback(async () => {
    try {
      const res = await api<{ profile: string }>(
        "GET",
        `/api/profile?level=${previewLevel}&with_sources=${withSources}`,
      );
      setPreview(res?.profile ?? "");
    } catch (err) {
      reportError(err, t("Unable to load profile"));
    }
  }, [previewLevel, withSources, t]);

  const loadFields = useCallback(async () => {
    try {
      const res = await api<ProfileFields>("GET", "/api/profile/fields");
      setFields(res);
      setFieldValues(res?.values ?? {});
      setShownOptional(
        (res?.schema ?? [])
          .filter((d) => !d.required
            && ((res?.values?.[d.key] ?? "").trim() || res?.sources?.[d.key]))
          .map((d) => d.key),
      );
      const manualRes = await api<{ content: string }>("GET", "/api/profile/manual");
      setManual(manualRes?.content ?? "");
      // History is returned with all fields mixed together; keeping 10 per field and fetching at the limit ensures later fields aren't missed
      const historyRes = await api<{ history: ProfileFieldHistoryItem[] }>(
        "GET", "/api/profile/fields/history?limit=200");
      setFieldHistory(historyRes?.history ?? []);
    } catch (err) {
      reportError(err, t("Unable to load profile fields"));
    }
  }, [t]);

  const loadClaims = useCallback(async () => {
    try {
      const [active, pending] = await Promise.all([
        api<{ claims: ProfileClaim[] }>("GET", "/api/profile/claims?status=active"),
        api<{ claims: ProfileClaim[] }>("GET", "/api/profile/claims?status=pending"),
      ]);
      setClaims(active?.claims ?? []);
      setPendingClaims(pending?.claims ?? []);
    } catch (err) {
      reportError(err, t("Unable to load claims"));
    }
  }, [t]);

  const loadDreams = useCallback(async () => {
    try {
      const res = await api<{ dreams: DreamItem[] }>("GET", "/api/profile/dreams");
      setDreams(res?.dreams ?? []);
    } catch (err) {
      reportError(err, t("Unable to load dreams"));
    }
  }, [t]);

  useEffect(() => {
    void Promise.all([loadStatus(), loadPreview(), loadFields()]).finally(() => setLoading(false));
  }, [loadStatus, loadPreview, loadFields]);

  useEffect(() => {
    if (tab === "claims") void loadClaims();
    if (tab === "dream") void loadDreams();
  }, [tab, loadClaims, loadDreams]);

  // Poll status every 4 seconds while a Dream is running
  const hasRunningDream = dreams.some((d) => d.status === "running");
  useEffect(() => {
    if (!hasRunningDream) return;
    const timer = setInterval(() => {
      void loadDreams();
      void loadStatus();
    }, 4000);
    return () => clearInterval(timer);
  }, [hasRunningDream, loadDreams, loadStatus]);

  const enabled = status?.enabled ?? false;

  const toggleEnabled = async () => {
    setBusy("toggle");
    try {
      await api("PUT", "/api/profile/settings", { enabled: !enabled });
      emitToast("success", !enabled ? t("Profile enabled") : t("Profile disabled"));
      await loadStatus();
      await loadPreview();
    } catch (err) {
      reportError(err, t("Unable to update settings"));
    } finally {
      setBusy("");
    }
  };

  const runDistill = async () => {
    setBusy("distill");
    try {
      const res = await api<{ skipped?: boolean; reason?: string; added?: number }>(
        "POST", "/api/profile/distill", {});
      if (res?.skipped) {
        emitToast("info", `${t("Distillation skipped")}: ${res.reason ?? ""}`);
      } else {
        emitToast("success", t("Distillation done: {n} claims added", { n: res?.added ?? 0 }));
      }
      await Promise.all([loadStatus(), loadPreview()]);
    } catch (err) {
      reportError(err, t("Distillation failed"));
    } finally {
      setBusy("");
    }
  };

  const runAudit = async () => {
    setBusy("audit");
    try {
      const res = await api<{ checked?: number }>("POST", "/api/profile/audit");
      emitToast("success", t("Audit done: {n} claims checked", { n: res?.checked ?? 0 }));
      await Promise.all([loadStatus(), loadClaims()]);
    } catch (err) {
      reportError(err, t("Audit failed"));
    } finally {
      setBusy("");
    }
  };

  // AI auto-fills fields and writes directly to DB; backend skips fields the user has manually edited
  const autofillFields = async () => {
    setBusy("suggest");
    try {
      const res = await api<{ applied: Record<string, string>; fields: ProfileFields }>(
        "POST", "/api/profile/fields/autofill");
      const count = Object.keys(res?.applied ?? {}).length;
      if (res?.fields) {
        setFields(res.fields);
        setFieldValues(res.fields.values ?? {});
      }
      if (!count) {
        emitToast("info", t("AI could not infer anything from your memories yet"));
      } else {
        emitToast("success", t("AI filled {n} fields — edit anything you like", { n: count }));
      }
      await loadFields();
    } catch (err) {
      reportError(err, t("AI could not fill the fields"));
    } finally {
      setBusy("");
    }
  };

  const saveFields = async () => {
    setBusy("fields");
    try {
      // Only submit fields the user actually changed: saving marks them as manual (AI won't overwrite afterward);
      // must not lock down unchanged AI-filled values along with them
      const changed: Record<string, string> = {};
      for (const [key, value] of Object.entries(fieldValues)) {
        if (String(value ?? "") !== String(fields?.values?.[key] ?? "")) {
          changed[key] = value;
        }
      }
      if (Object.keys(changed).length === 0) {
        emitToast("info", t("Nothing changed"));
        return;
      }
      const res = await api<ProfileFields>("PUT", "/api/profile/fields", { values: changed });
      setFields(res);
      setFieldValues(res?.values ?? {});
      emitToast("success", t("Fields saved"));
      await Promise.all([loadStatus(), loadFields()]);
    } catch (err) {
      reportError(err, t("Unable to save fields"));
    } finally {
      setBusy("");
    }
  };

  const saveManual = async () => {
    setBusy("manual");
    try {
      await api("PUT", "/api/profile/manual", { content: manual });
      emitToast("success", t("Manual profile saved"));
    } catch (err) {
      reportError(err, t("Unable to save manual profile"));
    } finally {
      setBusy("");
    }
  };

  const resolveClaim = async (claimId: number, action: "keep" | "delete") => {
    setBusy(`claim-${claimId}`);
    try {
      await api("POST", `/api/profile/claims/${claimId}/resolve`, { action });
      emitToast("success", action === "keep" ? t("Claim kept") : t("Claim deleted"));
      await Promise.all([loadClaims(), loadStatus()]);
    } catch (err) {
      reportError(err, t("Unable to resolve claim"));
    } finally {
      setBusy("");
    }
  };

  const startDream = async () => {
    setBusy("dream-start");
    try {
      await api("POST", "/api/profile/dreams", { scope: "all", instructions: dreamInstructions });
      setDreamInstructions("");
      emitToast("success", t("Dream started"));
      await loadDreams();
    } catch (err) {
      reportError(err, t("Unable to start dream"));
    } finally {
      setBusy("");
    }
  };

  const cancelDream = async (dreamId: number) => {
    try {
      await api("POST", `/api/profile/dreams/${dreamId}/cancel`);
      emitToast("info", t("Cancel requested"));
    } catch (err) {
      reportError(err, t("Unable to cancel dream"));
    }
  };

  const loadDiff = async (versionId: number) => {
    setBusy(`diff-${versionId}`);
    try {
      const diff = await api<DreamDiff>("GET", `/api/profile/versions/${versionId}/diff`);
      setDiffs((prev) => ({ ...prev, [versionId]: diff }));
    } catch (err) {
      reportError(err, t("Unable to load diff"));
    } finally {
      setBusy("");
    }
  };

  const decideVersion = async (versionId: number, action: "activate" | "discard") => {
    setBusy(`version-${versionId}`);
    try {
      await api("POST", `/api/profile/versions/${versionId}/${action}`);
      emitToast("success", action === "activate" ? t("Version activated") : t("Version discarded"));
      await Promise.all([loadDreams(), loadStatus(), loadPreview()]);
    } catch (err) {
      reportError(err, t("Unable to apply decision"));
    } finally {
      setBusy("");
    }
  };

  const activeByTier = useMemo(() => {
    const groups: Record<string, ProfileClaim[]> = { core: [], recent: [], map: [] };
    for (const claim of claims) {
      (groups[claim.tier] ?? (groups[claim.tier] = [])).push(claim);
    }
    return groups;
  }, [claims]);

  if (loading) {
    return (
      <Layout title={t("Profile")}>
        <LoadingLine label={t("Loading")} />
      </Layout>
    );
  }

  return (
    <Layout title={t("Profile")}>
      <div className="profile-layout">
        <Tabs
          items={[
            { key: "overview", label: t("Overview") },
            { key: "fields", label: t("About you") },
            { key: "claims", label: t("AI impressions") },
            { key: "dream", label: t("Dream") },
          ]}
          active={tab}
          onChange={setTab}
        />

        {tab === "overview" && (
          <>
            <div className="panel">
              <div className="panel-head">
                <span className="kicker">{t("Profile engine")}</span>
              </div>
              <div className="panel-body" style={{ display: "grid", gap: 14 }}>
                <label className="checkbox-row">
                  <input type="checkbox" checked={enabled} disabled={busy === "toggle"}
                         onChange={() => void toggleEnabled()} />
                  <span>{t("Enable AI profile distillation")}</span>
                </label>
                <p className="muted">
                  {t("The AI distills your memories into profile claims. Every claim is traceable to its source memory and reviewed before it appears. You can edit or disable everything here.")}
                </p>
                {status && (
                  <div className="stat-grid">
                    <div className="stat-cell">
                      <span className="kicker">{t("Active claims")}</span>
                      <strong>{status.claim_counts?.active ?? 0}</strong>
                    </div>
                    <div className="stat-cell">
                      <span className="kicker">{t("Pending issues")}</span>
                      <strong className={status.pending_issues ? "text-danger" : ""}>
                        {status.pending_issues}
                      </strong>
                    </div>
                    <div className="stat-cell">
                      <span className="kicker">{t("Last distillation")}</span>
                      <strong className="mono-sm">{status.last_distill?.day ?? "—"}</strong>
                    </div>
                    <div className="stat-cell">
                      <span className="kicker">{t("Last daily run")}</span>
                      <strong className="mono-sm">{status.last_daily_run?.day ?? "—"}</strong>
                    </div>
                  </div>
                )}
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                  <button className="btn" disabled={!enabled || busy === "distill"}
                          onClick={() => void runDistill()}>
                    <IconPlayerPlay size={16} aria-hidden="true" /> {t("Distill today now")}
                  </button>
                  <button className="btn" disabled={!enabled || busy === "audit"}
                          onClick={() => void runAudit()}>
                    <IconRefresh size={16} aria-hidden="true" /> {t("Run audit now")}
                  </button>
                </div>
              </div>
            </div>

            <div className="panel">
              <div className="panel-head">
                <span className="kicker">{t("What agents see")}</span>
                <div style={{ display: "flex", gap: 12, alignItems: "center", flexShrink: 0 }}>
                  <Select value={previewLevel} onChange={setPreviewLevel}
                    options={[
                      { value: "core", label: "core" },
                      { value: "standard", label: "standard" },
                      { value: "full", label: "full" },
                    ]} />
                  <label className="checkbox-row" style={{ whiteSpace: "nowrap" }}>
                    <input type="checkbox" checked={withSources}
                           onChange={(e) => setWithSources(e.target.checked)} />
                    <span>{t("Show sources")}</span>
                  </label>
                </div>
              </div>
              <div className="panel-body">
                <pre className="profile-preview mono-sm">{preview || t("Profile is empty")}</pre>
              </div>
            </div>
          </>
        )}

        {tab === "fields" && fields && (
          <>
            <div className="panel">
              <div className="panel-head">
                <span className="kicker">{t("Structured fields")}</span>
              </div>
              <div className="panel-body" style={{ display: "grid", gap: 14 }}>
                <p className="muted">{t("AI fills these in automatically from your memories. Change anything you like — once you edit a field, AI stops touching it.")}</p>
                <div className="profile-fields-grid">
                  {fields.schema
                    .filter((def) => def.required || shownOptional.includes(def.key))
                    .map((def) => {
                    const meta = fields.sources?.[def.key];
                    const source = meta?.source;
                    const past = historyByField[def.key] ?? [];
                    const open = openHistory[def.key] ?? false;
                    const inputId = `profile-field-${def.key}`;
                    return (
                      <div className={`profile-field-row ${open ? "history-open" : ""}`} key={def.key}>
                        <label className="profile-field-label" htmlFor={inputId}>
                          <span className="profile-field-name">
                            {t(def.label)}
                            {def.required && <em className="text-danger"> *</em>}
                          </span>
                        </label>
                        <input
                          id={inputId}
                          className="input"
                          value={fieldValues[def.key] ?? ""}
                          placeholder={def.hint ? t(def.hint) : ""}
                          onChange={(e) => setFieldValues((prev) => ({ ...prev, [def.key]: e.target.value }))}
                        />
                        <div className="profile-field-meta">
                          <div className="profile-field-meta-line">
                            {source && (
                              <span className={`profile-field-source ${source}`}>
                                {source === "manual" ? t("Edited by you") : t("AI-filled")}
                              </span>
                            )}
                            {source && meta?.updated_at && <span aria-hidden="true">·</span>}
                            {meta?.updated_at && (
                              <time dateTime={meta.updated_at}>
                                {t("Updated {time}", { time: formatWhen(meta.updated_at) })}
                              </time>
                            )}
                          </div>
                          {past.length > 0 && (
                            <button
                              type="button"
                              className="field-history-toggle"
                              onClick={() => setOpenHistory((prev) => ({ ...prev, [def.key]: !open }))}
                            >
                              {open ? <IconChevronDown size={13} aria-hidden="true" />
                                    : <IconChevronRight size={13} aria-hidden="true" />}
                              {t("{n} earlier versions", { n: past.length })}
                            </button>
                          )}
                        </div>
                        {open && (
                          <div className="field-history-panel">
                            <span className="field-history-heading">{t("Previous versions")}</span>
                            <ul className="field-history-list">
                              {past.map((h) => (
                                <li className="field-history-row mono-sm" key={h.id}>
                                  <span className="field-history-value">{h.value}</span>
                                  <span className="field-history-source">
                                    {h.source === "manual" ? t("Edited by you") : t("AI-filled")}
                                  </span>
                                  <time dateTime={h.archived_at}>
                                    {formatWhen(h.archived_at)}
                                  </time>
                                  <button
                                    type="button"
                                    className="btn small"
                                    onClick={() => setFieldValues((prev) => ({ ...prev, [def.key]: h.value }))}
                                  >
                                    {t("Restore")}
                                  </button>
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
                {fields.schema.some((def) => !def.required && !shownOptional.includes(def.key)) && (
                  <div className="profile-field-add">
                    <span className="kicker">{t("More fields")}</span>
                    <div className="chip-row">
                      {fields.schema
                        .filter((def) => !def.required && !shownOptional.includes(def.key))
                        .map((def) => (
                          <button
                            key={def.key}
                            type="button"
                            className="chip clickable"
                            onClick={() => setShownOptional((prev) => [...prev, def.key])}
                          >
                            <IconPlus size={13} aria-hidden="true" />
                            {t(def.label)}
                          </button>
                        ))}
                    </div>
                  </div>
                )}
                <div className="profile-fields-actions">
                  <button className="btn primary" disabled={busy === "fields"}
                          onClick={() => void saveFields()}>
                    {t("Save fields")}
                  </button>
                  <button className="btn" disabled={busy === "suggest"}
                          onClick={() => void autofillFields()}>
                    <IconSparkles size={16} aria-hidden="true" />
                    {busy === "suggest" ? t("Thinking…") : t("Let AI fill this in")}
                  </button>
                </div>
              </div>
            </div>

            <div className="panel">
              <div className="panel-head">
                <span className="kicker">{t("Manual profile")}</span>
              </div>
              <div className="panel-body" style={{ display: "grid", gap: 14 }}>
                <p className="muted">{t("Free-form notes about yourself (Markdown). Included verbatim in the profile output.")}</p>
                <textarea className="textarea" rows={10} value={manual}
                          onChange={(e) => setManual(e.target.value)} />
                <div>
                  <button className="btn primary" disabled={busy === "manual"}
                          onClick={() => void saveManual()}>
                    {t("Save manual profile")}
                  </button>
                </div>
              </div>
            </div>
          </>
        )}

        {tab === "claims" && (
          <>
            {pendingClaims.length > 0 && (
              <div className="panel">
                <div className="panel-head">
                  <span className="kicker text-danger">{t("Pending issues")}</span>
                </div>
                <div className="panel-body" style={{ display: "grid", gap: 10 }}>
                  {pendingClaims.map((claim) => (
                    <div className="claim-item pending" key={claim.id}>
                      <div className="claim-main">
                        <span className="badge">{t(PENDING_LABELS[claim.status] ?? claim.status)}</span>
                        <span>{claim.text}</span>
                        <SourceLinks claim={claim} />
                      </div>
                      <div className="claim-actions">
                        <button className="btn small" disabled={busy === `claim-${claim.id}`}
                                title={t("Still true, keep it")}
                                onClick={() => void resolveClaim(claim.id, "keep")}>
                          <IconCheck size={14} aria-hidden="true" /> {t("Keep")}
                        </button>
                        <button className="btn small danger" disabled={busy === `claim-${claim.id}`}
                                onClick={() => void resolveClaim(claim.id, "delete")}>
                          <IconTrash size={14} aria-hidden="true" /> {t("Delete")}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {(["core", "recent", "map"] as const).map((tier) => (
              <div className="panel" key={tier}>
                <div className="panel-head">
                  <span className="kicker">{t(TIER_LABELS[tier])}</span>
                  <span className="mono-sm muted">{activeByTier[tier]?.length ?? 0}</span>
                </div>
                <div className="panel-body" style={{ display: "grid", gap: 8 }}>
                  {(activeByTier[tier]?.length ?? 0) === 0 ? (
                    <span className="muted mono-sm">{t("No claims yet")}</span>
                  ) : (
                    activeByTier[tier].map((claim) => (
                      <div className="claim-item" key={claim.id}>
                        <div className="claim-main">
                          <span>{claim.text}</span>
                          <SourceLinks claim={claim} />
                        </div>
                        <div className="claim-actions">
                          <button className="btn small danger" disabled={busy === `claim-${claim.id}`}
                                  onClick={() => void resolveClaim(claim.id, "delete")}>
                            <IconTrash size={14} aria-hidden="true" />
                          </button>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            ))}
          </>
        )}

        {tab === "dream" && (
          <>
            {status?.dream_suggestion && (
              <div className="panel dream-suggestion">
                <div className="panel-body">
                  <span className="kicker">{t("Dream suggested")}</span>
                  <p>{status.dream_suggestion.reasons.join("; ")}</p>
                </div>
              </div>
            )}

            <div className="panel">
              <div className="panel-head">
                <span className="kicker">{t("Start a dream")}</span>
              </div>
              <div className="panel-body" style={{ display: "grid", gap: 14 }}>
                <p className="muted">{t("A dream reorganizes the profile offline: dedupe, merge, resolve conflicts and induce long-term patterns — always rewriting from original memory text. The result is a candidate version you review before it takes effect.")}</p>
                <label className="field">
                  <span>{t("Instructions (optional)")}</span>
                  <input className="input" value={dreamInstructions}
                         placeholder={t("e.g. focus on work-related claims")}
                         onChange={(e) => setDreamInstructions(e.target.value)} />
                </label>
                <div>
                  <button className="btn primary" disabled={!enabled || busy === "dream-start" || hasRunningDream}
                          onClick={() => void startDream()}>
                    <IconZzz size={16} aria-hidden="true" /> {t("Start dream")}
                  </button>
                </div>
              </div>
            </div>

            {dreams.length === 0 ? (
              <EmptyState message={t("No dreams yet")} />
            ) : (
              dreams.map((dream) => {
                const diff = dream.output_version_id ? diffs[dream.output_version_id] : undefined;
                return (
                  <div className="panel" key={dream.id}>
                    <div className="panel-head">
                      <span className="kicker">
                        Dream #{dream.id} · <span className={dream.status === "failed" ? "text-danger" : ""}>{dream.status}</span>
                      </span>
                      <span className="mono-sm muted">{dream.created_at?.slice(0, 16).replace("T", " ")}</span>
                    </div>
                    <div className="panel-body" style={{ display: "grid", gap: 10 }}>
                      {dream.trigger_reason && (
                        <span className="mono-sm muted">{t("Trigger")}: {dream.trigger_reason}</span>
                      )}
                      {dream.error && <span className="text-danger mono-sm">{dream.error}</span>}
                      {dream.status === "running" && (
                        <div>
                          <button className="btn small" onClick={() => void cancelDream(dream.id)}>
                            <IconX size={14} aria-hidden="true" /> {t("Cancel")}
                          </button>
                        </div>
                      )}
                      {dream.status === "review" && dream.output_version_id && (
                        <>
                          {!diff ? (
                            <div>
                              <button className="btn" disabled={busy === `diff-${dream.output_version_id}`}
                                      onClick={() => void loadDiff(dream.output_version_id!)}>
                                {t("Review changes")}
                              </button>
                            </div>
                          ) : (
                            <>
                              <div className="dream-diff">
                                {diff.added.map((c) => (
                                  <div className="diff-row added" key={`a${c.id}`}>
                                    <span className="mono-sm">+</span>
                                    <span>[{t(TIER_LABELS[c.tier] ?? c.tier)}] {c.text}</span>
                                  </div>
                                ))}
                                {diff.removed.map((c) => (
                                  <div className="diff-row removed" key={`r${c.id}`}>
                                    <span className="mono-sm">−</span>
                                    <span>[{t(TIER_LABELS[c.tier] ?? c.tier)}] {c.text}</span>
                                  </div>
                                ))}
                                {diff.modified.map((m) => (
                                  <div className="diff-row modified" key={`m${m.after.id}`}>
                                    <span className="mono-sm">~</span>
                                    <span>{m.before.text} → {m.after.text}</span>
                                  </div>
                                ))}
                                <span className="mono-sm muted">
                                  {t("{n} claims unchanged", { n: diff.unchanged_count })}
                                </span>
                              </div>
                              <div style={{ display: "flex", gap: 10 }}>
                                <button className="btn acid" disabled={busy === `version-${dream.output_version_id}`}
                                        onClick={() => void decideVersion(dream.output_version_id!, "activate")}>
                                  <IconCheck size={14} aria-hidden="true" /> {t("Activate")}
                                </button>
                                <button className="btn danger" disabled={busy === `version-${dream.output_version_id}`}
                                        onClick={() => void decideVersion(dream.output_version_id!, "discard")}>
                                  <IconTrash size={14} aria-hidden="true" /> {t("Discard")}
                                </button>
                              </div>
                            </>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </>
        )}
      </div>
    </Layout>
  );
}
