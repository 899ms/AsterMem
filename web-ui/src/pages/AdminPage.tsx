/**
 * Background: Admin page hosts three sections: sign-in settings (username/password/login protection toggle),
 * API Token management, and Danger zone.
 * Design intent: Username, password, and login protection share one "current password" field and one save
 * button—all three changes require the same credential confirmation, splitting into multiple forms would
 * force repeated password entry; after token creation, the full value appears only once (backend returns
 * plaintext only once), presented in a prominent panel + copy button; database clear uses "checkbox confirm +
 * secondary modal" two-step guard against accidental clicks.
 * Key constraints: Token list never shows full values; after clearing the DB, redirect to home forcing a full reload;
 * a persistent warning banner shows when still using default credentials (admin / admin).
 */
import { useCallback, useEffect, useState } from "react";
import { IconCopy, IconPlus, IconTrash, IconBan, IconRefreshAlert, IconDatabaseX, IconSeeding } from "@tabler/icons-react";
import { Layout } from "../components/Layout";
import { ConfirmModal } from "../components/Modal";
import { EmptyState } from "../components/EmptyState";
import { api, reportError } from "../api";
import { publishAuthSnapshot, useAuthSnapshot } from "../authState";
import { copyText } from "../clipboard";
import { emitToast } from "../toast";
import { useI18n } from "../i18n";
import type { TokenItem } from "../types";

interface AuthCheck {
  username?: string;
  login_required?: boolean;
  must_change_credentials?: boolean;
}

export function AdminPage() {
  const { t, locale } = useI18n();
  const { demoMode } = useAuthSnapshot();
  const [savedUsername, setSavedUsername] = useState("");
  const [username, setUsername] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [savedLoginRequired, setSavedLoginRequired] = useState(true);
  const [loginRequired, setLoginRequired] = useState(true);
  const [mustChange, setMustChange] = useState(false);
  const [changing, setChanging] = useState(false);
  const [tokens, setTokens] = useState<TokenItem[]>([]);
  const [tokenName, setTokenName] = useState("");
  const [tokenAdmin, setTokenAdmin] = useState(false);
  const [tokenDestructive, setTokenDestructive] = useState(false);
  const [createdToken, setCreatedToken] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  const [dangerChecked, setDangerChecked] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  const loadTokens = useCallback(async () => {
    // Tokens are agent credentials and the demo seals that endpoint, so asking would only turn
    // opening this page into an error toast.
    if (demoMode) return;
    try {
      const res = await api<unknown>("GET", "/api/tokens");
      const list = Array.isArray(res) ? res : (res as { tokens?: unknown })?.tokens;
      setTokens(Array.isArray(list) ? (list as TokenItem[]) : []);
    } catch (err) {
      reportError(err, t("Unable to load tokens"));
    }
  }, [t, demoMode]);

  const loadAuth = useCallback(async () => {
    try {
      const res = await api<AuthCheck>("GET", "/api/auth/check");
      setSavedUsername(res?.username ?? "");
      setUsername(res?.username ?? "");
      const required = res?.login_required !== false;
      setSavedLoginRequired(required);
      setLoginRequired(required);
      // Sync sidebar footer account name and sign-out entry visibility
      publishAuthSnapshot({ loginRequired: required, username: res?.username ?? "" });
      setMustChange(Boolean(res?.must_change_credentials));
    } catch (err) {
      reportError(err, t("Unable to load sign-in settings"));
    }
  }, [t]);

  useEffect(() => {
    void loadTokens();
    void loadAuth();
  }, [loadTokens, loadAuth]);

  const credentialsDirty = username.trim() !== savedUsername || Boolean(newPassword);
  const protectionDirty = loginRequired !== savedLoginRequired;

  /**
   * All three changes (username, password, login protection toggle) share current password for confirmation.
   * Credentials are changed first, then the toggle: toggling clears the session,
   * so reversing the order would cause subsequent requests to 401.
   */
  const saveSignIn = async () => {
    if (!currentPassword || (!credentialsDirty && !protectionDirty)) return;
    setChanging(true);
    try {
      if (credentialsDirty) {
        await api("POST", "/api/auth/credentials", {
          current_password: currentPassword,
          username: username.trim(),
          new_password: newPassword || null,
        });
      }
      if (protectionDirty) {
        await api("POST", "/api/auth/login-protection", {
          enabled: loginRequired,
          current_password: newPassword || currentPassword,
        });
      }
      emitToast("success", t("Sign-in settings saved"));
      setCurrentPassword("");
      setNewPassword("");
      await loadAuth();
    } catch (err) {
      reportError(err, t("Unable to save sign-in settings"));
    } finally {
      setChanging(false);
    }
  };

  const createToken = async () => {
    if (!tokenName.trim()) return;
    setBusy("create");
    try {
      const scopes = ["read", "write", "config"];
      if (tokenAdmin) scopes.push("admin");
      if (tokenDestructive) scopes.push("destructive");
      const res = await api<TokenItem>("POST", "/api/tokens", { name: tokenName.trim(), scopes });
      if (res?.token) {
        setCreatedToken(res.token);
      } else {
        emitToast("error", t("The backend did not return a token value"));
      }
      setTokenName("");
      setTokenAdmin(false);
      setTokenDestructive(false);
      void loadTokens();
    } catch (err) {
      reportError(err, t("Unable to create token"));
    } finally {
      setBusy("");
    }
  };

  const copyToken = async () => {
    if (!createdToken) return;
    if (await copyText(createdToken)) emitToast("success", t("Copied"));
    else emitToast("error", t("Copy failed, select the text manually"));
  };

  const revokeToken = async (token: TokenItem) => {
    if (!token.id) return;
    setBusy(`revoke-${token.id}`);
    try {
      await api("POST", `/api/tokens/${token.id}/revoke`);
      emitToast("success", t("Token revoked"));
      void loadTokens();
    } catch (err) {
      reportError(err, t("Unable to revoke token"));
    } finally {
      setBusy("");
    }
  };

  const deleteToken = async (token: TokenItem) => {
    if (!token.id) return;
    setBusy(`delete-${token.id}`);
    try {
      await api("DELETE", `/api/tokens/${token.id}`);
      emitToast("success", t("Token deleted"));
      void loadTokens();
    } catch (err) {
      reportError(err, t("Unable to delete token"));
    } finally {
      setBusy("");
    }
  };

  const clearDatabase = async () => {
    setBusy("clear");
    try {
      const res = await api<{ backup_path?: string }>("POST", "/api/clear-database");
      // Account has been reset, session invalidated—staying on page would trigger a series of 401s.
      // Full page redirect instead of router navigation: also discards all frontend cached state.
      const backup = res?.backup_path;
      sessionStorage.setItem(
        "astermem:reset-notice",
        backup ? t("Everything was cleared. Backup saved to {path}", { path: backup }) : t("Everything was cleared."),
      );
      window.location.href = "/login";
    } catch (err) {
      reportError(err, t("Unable to clear database"));
      setBusy("");
    }
  };

  const restart = async () => {
    setBusy("restart");
    try {
      await api("POST", "/api/restart");
      emitToast("info", t("Server is restarting, refresh in a few seconds"));
    } catch (err) {
      reportError(err, t("Unable to restart server"));
    } finally {
      setBusy("");
    }
  };

  const loadSamples = async () => {
    setBusy("samples");
    try {
      await api("POST", "/api/samples", { lang: locale });
      emitToast("success", t("Sample memories loaded"));
    } catch (err) {
      reportError(err, t("Unable to load samples"));
    } finally {
      setBusy("");
    }
  };

  return (
    <Layout title={t("Admin")}>
      <div className="admin-layout">
        <div className="panel">
          <div className="panel-head"><span className="kicker">{t("Sign-in")}</span></div>
          <div className="panel-body" style={{ display: "grid", gap: 14 }}>
            {mustChange && (
              <div style={{ padding: 12, border: "1px solid var(--ink)", background: "var(--acid)" }}>
                <span className="kicker">{t("You are still using the default credentials admin / admin. Set your own now.")}</span>
              </div>
            )}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <label className="field">
                <span>{t("Username")}</span>
                <input className="input mono" type="text" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
              </label>
              <label className="field">
                <span>{t("New password")}</span>
                <input className="input mono" type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)}
                  autoComplete="new-password" placeholder={t("Leave blank to keep it")} />
              </label>
            </div>
            <label className="checkbox-row">
              <input type="checkbox" checked={loginRequired} onChange={(e) => setLoginRequired(e.target.checked)} />
              {t("Require a username and password to open AsterMem")}
            </label>
            {!loginRequired && (
              <span className="kicker text-danger">
                {t("With sign-in off, anyone who can reach this address can read and edit your memories.")}
              </span>
            )}
            <label className="field" style={{ maxWidth: 320 }}>
              <span>{t("Current password")}</span>
              <input className="input mono" type="password" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} autoComplete="current-password" />
            </label>
            <button type="button" className="btn primary" style={{ justifySelf: "start" }}
              disabled={changing || !currentPassword || (!credentialsDirty && !protectionDirty)} onClick={saveSignIn}>
              {changing ? t("Saving") : t("Save")}
            </button>
          </div>
        </div>

        <div className="panel admin-tokens">
          <div className="panel-head"><span className="kicker">{t("API tokens")}</span></div>
          <div className="panel-body" style={{ display: "grid", gap: 14 }}>
            {createdToken && (
              <div style={{ padding: 14, border: "1px solid var(--ink)", background: "var(--acid)", display: "grid", gap: 8 }}>
                <span className="kicker">{t("Copy this token now, it will not be shown again")}</span>
                <code className="mono-sm" style={{ wordBreak: "break-all" }}>{createdToken}</code>
                <div style={{ display: "flex", gap: 8 }}>
                  <button type="button" className="btn small" onClick={copyToken}>
                    <IconCopy aria-hidden="true" />{t("Copy")}
                  </button>
                  <button type="button" className="btn small" onClick={() => setCreatedToken(null)}>{t("Dismiss")}</button>
                </div>
              </div>
            )}
            <div style={{ display: "grid", gap: 10 }}>
              <div style={{ display: "flex", gap: 10 }}>
                <input className="input" style={{ maxWidth: 300 }} value={tokenName}
                  onChange={(e) => setTokenName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); void createToken(); } }}
                  placeholder={t("Token name")} />
                <span
                  className="button-tooltip"
                  data-tooltip={!tokenName.trim() ? t("Enter a token name on the left first") : undefined}
                >
                  <button type="button" className="btn" disabled={busy === "create" || !tokenName.trim()} onClick={createToken}>
                    <IconPlus aria-hidden="true" />{t("Create token")}
                  </button>
                </span>
              </div>
              <span className="mono-sm muted">{t("Memory and provider access is enabled by default.")}</span>
              <label className="checkbox-row">
                <input type="checkbox" checked={tokenAdmin} onChange={(e) => setTokenAdmin(e.target.checked)} />
                {t("Allow account, token, and log management")}
              </label>
              <label className="checkbox-row text-danger">
                <input type="checkbox" checked={tokenDestructive} onChange={(e) => setTokenDestructive(e.target.checked)} />
                {t("Allow destructive actions such as clearing data and restarting")}
              </label>
            </div>
            {tokens.length === 0 ? (
              <EmptyState message={t("No API tokens yet")} />
            ) : (
              <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t("Name")}</th>
                    <th>{t("Prefix")}</th>
                    <th>{t("Permissions")}</th>
                    <th>{t("Created")}</th>
                    <th>{t("Last used")}</th>
                    <th>{t("Actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {tokens.map((token, i) => (
                    <tr key={token.id ?? i}>
                      <td>{token.name ?? ""}{token.revoked ? ` (${t("revoked")})` : ""}</td>
                      <td className="mono-sm">{token.prefix ?? ""}</td>
                      <td className="mono-sm">{(token.scopes ?? []).join(", ")}</td>
                      <td className="mono-sm">{(token.created_at ?? "").slice(0, 10)}</td>
                      <td className="mono-sm">{(token.last_used_at ?? "").slice(0, 16).replace("T", " ") || "-"}</td>
                      <td>
                        <div style={{ display: "flex", gap: 6 }}>
                          {!token.revoked && (
                            <button type="button" className="btn small" disabled={busy === `revoke-${token.id}`} onClick={() => revokeToken(token)}>
                              <IconBan aria-hidden="true" />{t("Revoke")}
                            </button>
                          )}
                          <button type="button" className="btn small danger" disabled={busy === `delete-${token.id}`} onClick={() => deleteToken(token)}>
                            <IconTrash aria-hidden="true" />{t("Delete")}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            )}
          </div>
        </div>

        <div className="panel" style={{ borderColor: "var(--danger)" }}>
          <div className="panel-head"><span className="kicker text-danger">{t("Danger zone")}</span></div>
          <div className="panel-body" style={{ display: "grid", gap: 14 }}>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <button type="button" className="btn" disabled={busy === "samples"} onClick={loadSamples}>
                <IconSeeding aria-hidden="true" />{t("Load sample data")}
              </button>
              <button type="button" className="btn" disabled={busy === "restart"} onClick={restart}>
                <IconRefreshAlert aria-hidden="true" />{t("Restart server")}
              </button>
            </div>
            <label className="checkbox-row text-danger">
              <input type="checkbox" checked={dangerChecked} onChange={(e) => setDangerChecked(e.target.checked)} />
              {t("I understand that this resets AsterMem to a factory state and signs me out")}
            </label>
            <button type="button" className="btn danger" style={{ justifySelf: "start" }}
              disabled={!dangerChecked} onClick={() => setConfirmClear(true)}>
              <IconDatabaseX aria-hidden="true" />{t("Reset everything")}
            </button>
          </div>
        </div>
      </div>

      {confirmClear && (
        <ConfirmModal
          title={t("Reset everything")}
          message={
            <div className="reset-warning">
              <p>{t("This wipes AsterMem back to a fresh install. The following are deleted for good:")}</p>
              <ul>
                <li>{t("Every memory, along with its Markdown source file")}</li>
                <li>{t("All segments, tags, entities, and the knowledge graph")}</li>
                <li>{t("The entire profile — AI claims, structured fields, field history, and your manual profile")}</li>
                <li>{t("Vector index, full-text index, and uploaded images")}</li>
                <li>{t("Your account and password, reset back to admin / admin")}</li>
                <li>{t("Every API token, so any connected AI stops working until you issue new ones")}</li>
              </ul>
              <p className="reset-warning-note">
                {t("A full copy of data/ is saved to backups/ first. You will be signed out when this finishes.")}
              </p>
            </div>
          }
          confirmLabel={t("Reset everything")}
          cancelLabel={t("Cancel")}
          danger
          busy={busy === "clear"}
          onConfirm={clearDatabase}
          onCancel={() => setConfirmClear(false)}
        />
      )}
    </Layout>
  );
}
