/**
 * Background: List/search/graph pages need a unified empty state display when there's no data.
 * Design intent: Dashed border + Tabler icon + single-line description, consistent with the editorial style,
 * preventing inconsistent "no data" presentations across pages.
 * Key constraint: Text is passed in pre-translated by the caller.
 */
import type { ReactNode } from "react";
import { IconInbox } from "@tabler/icons-react";

export function EmptyState({ message, action }: { message: string; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <IconInbox aria-hidden="true" />
      <span className="mono-sm">{message}</span>
      {action}
    </div>
  );
}

/**
 * Background: Loading placeholders need a unified style.
 * Design intent: Mono uppercase small text + breathing animation, more aligned with the editorial magazine feel than a spinner.
 * Key constraint: Only for full-area loading; busy state inside buttons is expressed with disabled.
 */
export function LoadingLine({ label }: { label: string }) {
  return <div className="loading-line">{label}</div>;
}
