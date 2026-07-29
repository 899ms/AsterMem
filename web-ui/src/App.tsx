/**
 * Background: SPA routing entry. When the backend returns 401 for unauthenticated requests,
 * api.ts globally redirects to /login, but on first load we should proactively check the
 * auth state to avoid a 401 flash on every page.
 * Design intent: AuthGate calls /api/auth/check on mount, redirecting to login if not authenticated;
 * the Playground route is only registered in DEV builds (import.meta.env.DEV).
 * Key constraint: Uses BrowserRouter (in production FastAPI serves index.html as a fallback).
 */
import { useEffect, useState, type ReactNode } from "react";
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { I18nProvider, useI18n } from "./i18n";
import { ToastHost } from "./toast";
import { api } from "./api";
import { publishAuthSnapshot } from "./authState";
import { useOutputLanguageSync } from "./outputLanguage";
import { LoadingLine } from "./components/EmptyState";
import { LandingPage } from "./pages/LandingPage";
import { LoginPage } from "./pages/LoginPage";
import { HomePage } from "./pages/HomePage";
import { MemoryListPage } from "./pages/MemoryListPage";
import { MemoryEditorPage } from "./pages/MemoryEditorPage";
import { MemoryViewPage } from "./pages/MemoryViewPage";
import { TagsPage } from "./pages/TagsPage";
import { ImportPage } from "./pages/ImportPage";
import { ExplorePage } from "./pages/ExplorePage";
import { GraphPage } from "./pages/GraphPage";
import { LogsPage } from "./pages/LogsPage";
import { UsagePage } from "./pages/UsagePage";
import { SettingsPage } from "./pages/SettingsPage";
import { AdminPage } from "./pages/AdminPage";
import { ProfilePage } from "./pages/ProfilePage";
import { MethodologyPage } from "./pages/MethodologyPage";
import { PlaygroundPage } from "./pages/PlaygroundPage";

/**
 * Background: When a protected page is refreshed, we need to verify the session is still valid.
 * Design intent: The check endpoint uses skipAuthRedirect to get the raw 401,
 * letting this component decide on the redirect, avoiding a race between api.ts global
 * redirect and React rendering.
 * Key constraint: On probe failure (network error), rendering is allowed to proceed—
 * subsequent business requests will handle it, preventing the whole site from
 * freezing on loading when offline.
 */
function AuthGate({ children }: { children: ReactNode }) {
  const { t, locale } = useI18n();
  const location = useLocation();
  const [state, setState] = useState<"checking" | "ok" | "anonymous">("checking");

  // The reader's language has to reach the backend for AI-generated text to come back in it.
  // Hanging it off the session probe covers both switching language and never touching the
  // picker, and keeps the write out of anonymous pages where it could only 401.
  useOutputLanguageSync(locale, state === "ok");

  useEffect(() => {
    let cancelled = false;
    api<{ authenticated?: boolean; login_required?: boolean; username?: string; demo_mode?: boolean }>(
      "GET",
      "/api/auth/check",
      undefined,
      { skipAuthRedirect: true },
    )
      .then((res) => {
        if (cancelled) return;
        // Reuse probe result for sidebar footer (account name & login protection status)
        publishAuthSnapshot({
          loginRequired: res?.login_required !== false,
          username: res?.username ?? "",
          demoMode: Boolean(res?.demo_mode),
        });
        setState(res?.authenticated ? "ok" : "anonymous");
      })
      .catch((err) => {
        console.error("[AsterMem] auth check failed", err);
        if (!cancelled) setState("ok");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state === "checking") return <LoadingLine label={t("Loading")} />;
  if (state === "anonymous") {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  return <>{children}</>;
}

export function App() {
  return (
    <I18nProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/methodology" element={<MethodologyPage />} />
          <Route
            path="/*"
            element={
              <AuthGate>
                <Routes>
                  <Route path="/home" element={<HomePage />} />
                  <Route path="/memories" element={<MemoryListPage />} />
                  <Route path="/new" element={<MemoryEditorPage />} />
                  <Route path="/edit/:id" element={<MemoryEditorPage />} />
                  <Route path="/view/:id" element={<MemoryViewPage />} />
                  <Route path="/tags" element={<TagsPage />} />
                  <Route path="/import" element={<ImportPage />} />
                  <Route path="/explore" element={<ExplorePage />} />
                  <Route path="/graph" element={<GraphPage />} />
                  <Route path="/profile" element={<ProfilePage />} />
                  <Route path="/logs" element={<LogsPage />} />
                  <Route path="/usage" element={<UsagePage />} />
                  <Route path="/settings" element={<SettingsPage />} />
                  <Route path="/admin" element={<AdminPage />} />
                  {import.meta.env.DEV && <Route path="/playground" element={<PlaygroundPage />} />}
                  <Route path="*" element={<Navigate to="/home" replace />} />
                </Routes>
              </AuthGate>
            }
          />
        </Routes>
      </BrowserRouter>
      <ToastHost />
    </I18nProvider>
  );
}
