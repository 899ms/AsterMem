/**
 * Background: All pages except login share a "fixed left sidebar + right content area" shell.
 * Design intent: Sidebar aligned with the Asterove console—brand name + subtitle two-line header,
 * mono-numbered (01, 02…) text-only navigation (no icons; numbers serve as visual anchors),
 * active state fills the row with acid green; footer shows "current account + role" plus a
 * text-style sign-out (no button frame).
 * Top bar: left side follows nav numbering as breadcrumb (`03 / Tags`), right side only has the locale switcher.
 * Narrow screen (≤860px): sidebar becomes a left drawer, with a separate bottom tab bar for five high-frequency entries.
 * Key constraints: All nav labels go through t(); numbering derives from NAV_ITEMS order—no manual edits needed;
 * instances with login protection off have no session to sign out of, so the sign-out entry is hidden;
 * the drawer must auto-close on route change, otherwise it blocks the new page after navigation.
 */
import { NavLink, useLocation } from "react-router-dom";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { IconBrandGithub, IconBrandX, IconX } from "@tabler/icons-react";
import { LocaleSwitcher, useI18n } from "../i18n";
import { api, reportError } from "../api";
import { useAuthSnapshot } from "../authState";
import { SOURCE_URL, X_HANDLE, X_URL } from "../license";
import { MobileTabBar } from "./MobileTabBar";
import { DemoBadge } from "./DemoBadge";

type NavEntry = { to: string; label: string; end?: boolean } | { group: string };

const NAV_ITEMS: NavEntry[] = [
  { to: "/home", label: "Home", end: true },
  { to: "/new", label: "Add with AI" },
  { to: "/memories", label: "Memories" },
  { to: "/explore", label: "Explore" },
  { group: "Organize" },
  { to: "/tags", label: "Tags" },
  { to: "/graph", label: "Graph" },
  { to: "/profile", label: "Profile" },
  { group: "Insights" },
  { to: "/usage", label: "Usage" },
  { to: "/logs", label: "Logs" },
  { to: "/methodology", label: "Methodology" },
  { group: "Manage" },
  { to: "/import", label: "Import / Export" },
  { to: "/settings", label: "Settings" },
  { to: "/admin", label: "Admin" },
  { to: "/security", label: "Security" },
];

if (import.meta.env.DEV) {
  NAV_ITEMS.push({ to: "/playground", label: "Playground" });
}

function isLink(entry: NavEntry): entry is { to: string; label: string; end?: boolean } {
  return "to" in entry;
}

const navIndex = (position: number) => String(position + 1).padStart(2, "0");

/** Deep link parent mapping: /view/:id and /edit/:id belong under "Memories" */
const DEEP_LINK_PARENT: Array<[string, string]> = [
  ["/view", "/memories"],
  ["/edit", "/memories"],
];

export function Layout({ title, actions, toolbar, fill, children }: {
  title: string;
  actions?: ReactNode;
  /** Persistent toolbar below the header (e.g. search bar), frozen together with the header */
  toolbar?: ReactNode;
  /** Page manages its own scrolling: content fills the viewport, header and toolbar don't scroll with content */
  fill?: boolean;
  children: ReactNode;
}) {
  const { t } = useI18n();
  const location = useLocation();
  const { loginRequired, username, demoMode } = useAuthSnapshot();
  const [drawerOpen, setDrawerOpen] = useState(false);

  // The demo refuses /api/security outright — it would report the host's own defences to anyone,
  // and a demo has no sign-in. Showing an entry that can only error is worse than not showing it.
  const navEntries = useMemo(
    () => (demoMode ? NAV_ITEMS.filter((entry) => !isLink(entry) || entry.to !== "/security") : NAV_ITEMS),
    [demoMode],
  );
  const navLinks = useMemo(() => navEntries.filter(isLink), [navEntries]);

  const parent = DEEP_LINK_PARENT.find(([prefix]) => location.pathname.startsWith(prefix))?.[1];
  const activePath = parent ?? location.pathname;
  const currentLinkIndex = Math.max(
    navLinks.findIndex((item) => (item.end ? activePath === item.to : activePath.startsWith(item.to))),
    0,
  );

  useEffect(() => setDrawerOpen(false), [location.pathname]);

  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDrawerOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  /**
   * Background: Sign-out must first have the server destroy the session, then redirect to login.
   * Design intent: Even if the logout endpoint fails, show an error to the user—don't swallow silently.
   * Key constraint: On success, hard-redirect to /login to clear all in-memory state.
   */
  const handleLogout = async () => {
    try {
      await api("POST", "/api/auth/logout");
      window.location.href = "/login";
    } catch (err) {
      reportError(err, t("Unable to sign out"));
    }
  };

  return (
    <div className="app-shell">
      <aside className={drawerOpen ? "sidebar open" : "sidebar"}>
        <div className="sidebar-brand">
          <img src="/astermem-icon.png" alt="" aria-hidden="true" />
          <span>
            <strong>AsterMem</strong>
            <em>{t("Personal memory service")}</em>
          </span>
          <button
            type="button"
            className="drawer-close"
            aria-label={t("Close menu")}
            onClick={() => setDrawerOpen(false)}
          >
            <IconX aria-hidden="true" />
          </button>
        </div>
        <nav className="sidebar-nav">
          {(() => {
            let linkIdx = 0;
            return navEntries.map((entry, i) => {
              if (!isLink(entry)) {
                return <span key={`g-${i}`} className="sidebar-group">{t(entry.group)}</span>;
              }
              const pos = linkIdx++;
              return (
                <NavLink
                  key={entry.to}
                  to={entry.to}
                  end={entry.end}
                  className={() => (pos === currentLinkIndex ? "active" : "")}
                >
                  <em aria-hidden="true">{navIndex(pos)}</em>
                  {t(entry.label)}
                </NavLink>
              );
            });
          })()}
        </nav>
        <div className="sidebar-foot">
          <span className="sidebar-account">
            <strong>{demoMode ? t("Demo") : username || "—"}</strong>
            <em>{demoMode ? t("Read-only") : loginRequired ? t("Owner") : t("No sign-in")}</em>
          </span>
          {loginRequired && !demoMode && (
            <button type="button" className="linklike" onClick={handleLogout}>
              {t("Sign out")}
            </button>
          )}
        </div>
      </aside>
      <div className="main-area">
        <header className="topbar">
          <span className="crumb">
            <em>{navIndex(currentLinkIndex)}</em>
            {t(navLinks[currentLinkIndex]?.label ?? "Home")}
          </span>
          <div className="topbar-right">
            {demoMode && <DemoBadge />}
            <a
              className="topbar-icon"
              href={SOURCE_URL}
              target="_blank"
              rel="noreferrer"
              aria-label={t("Source code")}
              title={t("Source code")}
            >
              <IconBrandGithub aria-hidden="true" />
            </a>
            <a
              className="topbar-icon"
              href={X_URL}
              target="_blank"
              rel="noreferrer"
              aria-label={t("Questions? {handle} on X", { handle: X_HANDLE })}
              title={t("Questions? {handle} on X", { handle: X_HANDLE })}
            >
              <IconBrandX aria-hidden="true" />
            </a>
            <LocaleSwitcher />
          </div>
        </header>
        <main className={fill ? "page-body page-body-fill" : "page-body"}>
          {/* Scroll container fills full width (scrollbar hugs the right edge); max-width is handled by page-inner and centered */}
          <div className="page-inner">
            <div className="page-head">
              <h1 className="page-title">{title}</h1>
              {actions && <div className="page-head-actions">{actions}</div>}
            </div>
            {toolbar && <div className="page-toolbar">{toolbar}</div>}
            {children}
          </div>
        </main>
      </div>
      {drawerOpen && (
        <div className="drawer-scrim" onClick={() => setDrawerOpen(false)} aria-hidden="true" />
      )}
      <MobileTabBar activePath={activePath} onMore={() => setDrawerOpen(true)} />
    </div>
  );
}
