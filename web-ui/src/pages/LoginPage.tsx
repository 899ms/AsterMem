/**
 * Background: AsterMem is a single-user self-hosted service with one admin account,
 * default credentials admin / admin; users change to their own credentials in the admin page after first login.
 * Design intent: Fullscreen grid-paper background + hard-shadow card; after login success, redirect to ?next= path;
 * if still using default credentials, prompt to change them in Admin after login.
 * Key constraint: Login password field allows type="password" (project convention only requires text for API Key inputs);
 * both inputs must be wrapped in <form> (so the browser recognizes it as a login form—password manager can save,
 * Enter submission is handled by form submit semantics);
 * instances with login protection off skip directly to home, preventing users from getting stuck on an unnecessary login page.
 */
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { IconArrowRight } from "@tabler/icons-react";
import { api, reportError } from "../api";
import { emitToast } from "../toast";
import { LocaleSwitcher, useI18n } from "../i18n";

interface AuthCheck {
  authenticated?: boolean;
  login_required?: boolean;
  must_change_credentials?: boolean;
}

export function LoginPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  // After factory reset, the page redirects here; carry over the backup path to tell the user where data was saved
  useEffect(() => {
    const notice = sessionStorage.getItem("astermem:reset-notice");
    if (notice) {
      sessionStorage.removeItem("astermem:reset-notice");
      emitToast("info", notice);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    api<AuthCheck>("GET", "/api/auth/check", undefined, { skipAuthRedirect: true })
      .then((res) => {
        if (!cancelled && res?.login_required === false) navigate("/home", { replace: true });
      })
      .catch((err) => {
        console.error("[AsterMem] auth check failed", err);
      });
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  const submit = async () => {
    if (!username.trim() || !password || busy) return;
    setBusy(true);
    try {
      const res = await api<AuthCheck>(
        "POST",
        "/api/auth/login",
        { username: username.trim(), password },
        { skipAuthRedirect: true },
      );
      const next = params.get("next");
      navigate(next && next.startsWith("/") ? decodeURIComponent(next) : "/home", { replace: true });
      if (res?.must_change_credentials) {
        emitToast("info", t("You are still using the default credentials, change them in Admin"));
      }
    } catch (err) {
      reportError(err, t("Incorrect username or password"));
      setBusy(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void submit();
  };

  return (
    <div className="login-screen">
      <div style={{ position: "fixed", top: 20, right: 22 }}>
        <LocaleSwitcher />
      </div>
      {/* Wrap inputs in a real form so the browser recognizes it as a login form (password manager can save),
          and Enter submission is handled by form submit semantics without per-input key listeners */}
      <form className="login-card" onSubmit={handleSubmit}>
        <span className="kicker">{t("Personal memory service")}</span>
        <h1>
          <i aria-hidden="true" />
          ASTERMEM
        </h1>
        <div className="field" style={{ marginBottom: 14 }}>
          <span>{t("Username")}</span>
          <input
            className="input mono"
            type="text"
            autoFocus
            autoComplete="username"
            name="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="admin"
          />
        </div>
        <div className="field" style={{ marginBottom: 20 }}>
          <span>{t("Password")}</span>
          <input
            className="input mono"
            type="password"
            autoComplete="current-password"
            name="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
          />
        </div>
        <button type="submit" className="btn primary" style={{ width: "100%", justifyContent: "space-between" }} disabled={busy || !username.trim() || !password}>
          {busy ? t("Signing in") : t("Sign in")}
          <IconArrowRight aria-hidden="true" />
        </button>
      </form>
    </div>
  );
}
