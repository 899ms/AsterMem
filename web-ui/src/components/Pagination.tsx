/**
 * Background: Memory list and logs page both use limit/offset pagination.
 * Design intent: Minimal "Previous / page number / Next" mono small-text control;
 * when total is missing (backend field tolerance), infers whether next page exists from
 * whether the current page is full.
 * Key constraint: Offset semantics are owned by the parent; this component is pure display + callback.
 */
import { useI18n } from "../i18n";

export function Pagination({ offset, limit, total, pageCount, onPage }: {
  offset: number;
  limit: number;
  /** Backend may not return total; when undefined, pageCount is used to infer */
  total?: number;
  /** Actual item count on current page, used to determine if there's a next page when total is absent */
  pageCount: number;
  onPage: (nextOffset: number) => void;
}) {
  const { t } = useI18n();
  const page = Math.floor(offset / limit) + 1;
  const totalPages = total !== undefined ? Math.max(1, Math.ceil(total / limit)) : undefined;
  const hasPrev = offset > 0;
  const hasNext = totalPages !== undefined ? page < totalPages : pageCount >= limit;

  if (!hasPrev && !hasNext) return null;

  return (
    <div className="pagination">
      <button type="button" className="btn small" disabled={!hasPrev} onClick={() => onPage(Math.max(0, offset - limit))}>
        {t("Previous")}
      </button>
      <span className="muted">
        {totalPages !== undefined
          ? t("Page {current} of {total}", { current: page, total: totalPages })
          : t("Page {current}", { current: page })}
      </span>
      <button type="button" className="btn small" disabled={!hasNext} onClick={() => onPage(offset + limit)}>
        {t("Next")}
      </button>
    </div>
  );
}
