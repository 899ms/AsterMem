/**
 * Background: A visitor landing deep in the demo — on /graph or a memory page — sees a working app
 * with no sign that it is a showcase. The read-only toast only appears once they try to write, and
 * the explanation on the home page is not where they are.
 * Design intent: A badge that rides along in the top bar on every page, opening the three things
 * such a visitor needs: this is a demo, here is how to run a real one, here is where to ask.
 * Key constraint: Click to open rather than hover, so it works on touch; the panel closes on any
 * outside click, matching LocaleSwitcher.
 */
import { useEffect, useState } from "react";
import { IconBrandGithub, IconBrandX, IconInfoCircle } from "@tabler/icons-react";
import { useI18n } from "../i18n";
import { SOURCE_URL, X_HANDLE, X_URL } from "../license";

export function DemoBadge() {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [open]);

  return (
    <div className="demo-badge" onClick={(e) => e.stopPropagation()}>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}>
        <IconInfoCircle aria-hidden="true" />
        <span>{t("Demo")}</span>
      </button>
      {open && (
        <div className="demo-badge-panel">
          <p className="demo-badge-lead">{t("You are looking at a public demo with a sample library. Everything is read-only and nothing you do here is kept.")}</p>
          <a href={SOURCE_URL} target="_blank" rel="noreferrer">
            <IconBrandGithub aria-hidden="true" />
            <span>
              <strong>{t("Run your own")}</strong>
              {t("Free software, one command to start, memories stay on your disk.")}
            </span>
          </a>
          <a href={X_URL} target="_blank" rel="noreferrer">
            <IconBrandX aria-hidden="true" />
            <span>
              <strong>{t("Questions? {handle} on X", { handle: X_HANDLE })}</strong>
              {t("The demo has no inbox, so this is the way to reach us.")}
            </span>
          </a>
        </div>
      )}
    </div>
  );
}
