/**
 * Background: On narrow screens the sidebar collapses into a drawer, but since the drawer is
 * hidden behind a tap, high-frequency pages shouldn't require opening the drawer every time.
 * Design intent: Pin five high-frequency entries at the bottom—Home, Memories, Ask, Graph,
 * plus a "More" slot that opens the drawer; the center Ask slot is raised with acid green,
 * serving as the app's single primary action point ("ask your memory base directly").
 * Key constraint: Active state reuses Layout's computed activePath (deep links like /view/:id
 * map to Memories), not NavLink's built-in matching—otherwise all bottom tabs deactivate
 * when entering from a detail page; all labels go through t(), icons use Tabler locally-bundled SVGs.
 */
import { Link } from "react-router-dom";
import { IconHome, IconNotes, IconSparkles, IconTags, IconChartDots3, IconMenu2 } from "@tabler/icons-react";
import { useI18n } from "../i18n";
import { useAuthSnapshot } from "../authState";

export function MobileTabBar({ activePath, onMore }: { activePath: string; onMore: () => void }) {
  const { t } = useI18n();
  const { demoMode } = useAuthSnapshot();
  const cls = (to: string) => (activePath === to ? "active" : "");

  return (
    <nav className="tabbar" aria-label={t("Navigation")}>
      <Link to="/home" className={cls("/home")}>
        <IconHome aria-hidden="true" />
        {t("Home")}
      </Link>

      <Link to="/memories" className={cls("/memories")}>
        <IconNotes aria-hidden="true" />
        {t("Memories")}
      </Link>

      {/* The demo has no AI channel, so the primary slot points at tag browsing instead of Ask. */}
      {demoMode ? (
        <Link to="/tags" className={cls("/tags")}>
          <IconTags aria-hidden="true" />
          {t("Tags")}
        </Link>
      ) : (
        <Link to="/explore" className={`tabbar-ask ${cls("/explore")}`}>
          <span className="tabbar-ask-badge">
            <IconSparkles aria-hidden="true" />
          </span>
          {t("Ask")}
        </Link>
      )}

      <Link to="/graph" className={cls("/graph")}>
        <IconChartDots3 aria-hidden="true" />
        {t("Graph")}
      </Link>

      <button type="button" onClick={onMore}>
        <IconMenu2 aria-hidden="true" />
        {t("More")}
      </button>
    </nav>
  );
}
