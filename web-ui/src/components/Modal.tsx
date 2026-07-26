/**
 * Background: Tag merge/rename, dangerous action confirmation, log details etc. all need modals.
 * Design intent: One generic Modal (backdrop click to close + hard-shadow editorial feel)
 * and one two-button ConfirmModal, covering 90% of site-wide modal needs without per-page reimplementation.
 * Key constraint: All button text is passed in pre-translated by the caller; danger confirmation uses the danger variant.
 */
import type { ReactNode } from "react";
import { IconX } from "@tabler/icons-react";

export function Modal({ title, wide, onClose, children, footer }: {
  title: string;
  wide?: boolean;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className={`modal ${wide ? "wide" : ""}`} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="kicker">{title}</span>
          <button type="button" onClick={onClose} aria-label="Close">
            <IconX size={16} stroke={1.5} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

/**
 * Background: Destructive operations like deleting tags or clearing the database require explicit confirmation.
 * Design intent: Confirm button supports busy state to prevent double-submission; danger style signals irreversibility.
 * Key constraint: onConfirm handles errors internally; this component does not swallow exceptions.
 */
export function ConfirmModal({ title, message, confirmLabel, cancelLabel, danger, busy, onConfirm, onCancel }: {
  title: string;
  message: ReactNode;
  confirmLabel: string;
  cancelLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal
      title={title}
      onClose={onCancel}
      footer={
        <>
          <button type="button" className="btn" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className={`btn ${danger ? "danger" : "primary"}`}
            disabled={busy}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </>
      }
    >
      <div style={{ fontSize: 14 }}>{message}</div>
    </Modal>
  );
}
