/**
 * Background: The entire site needs a lightweight toast system for error messages and action feedback,
 * and non-React modules like api.ts also need to trigger toasts (e.g. unified request failures).
 * Design intent: Module-level event subscription + <ToastHost/> rendering; emitToast() can be called
 * from any code path without depending on the Context hierarchy, decoupling fetch wrappers from the React tree.
 * Key constraint: Toast content is already i18n-translated by the caller; this module only displays and auto-dismisses.
 */
import { useEffect, useState } from "react";

export type ToastKind = "info" | "success" | "error";

export interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
}

type Listener = (item: ToastItem) => void;

let seq = 0;
const listeners = new Set<Listener>();

/**
 * Background: Error toasts may come from React components or from api.ts global error handling.
 * Design intent: Pub/sub decoupling; if ToastHost is not yet mounted, silently discard
 * (extreme timing like login redirect); never block the caller's flow.
 * Key constraint: message must be the final translated text.
 */
export function emitToast(kind: ToastKind, message: string) {
  const item: ToastItem = { id: ++seq, kind, message };
  listeners.forEach((fn) => fn(item));
}

/**
 * Background: Toasts need to render fixed at the bottom-right of the viewport and auto-dismiss.
 * Design intent: Host internally holds its own queue state; removes after 5 seconds, errors kept 8 seconds for readability.
 * Key constraint: Only one ToastHost is mounted at the App root.
 */
export function ToastHost() {
  const [items, setItems] = useState<ToastItem[]>([]);

  useEffect(() => {
    const listener: Listener = (item) => {
      setItems((prev) => [...prev.slice(-4), item]);
      const ttl = item.kind === "error" ? 8000 : 5000;
      window.setTimeout(() => {
        setItems((prev) => prev.filter((x) => x.id !== item.id));
      }, ttl);
    };
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  if (items.length === 0) return null;

  return (
    <div className="toast-host">
      {items.map((item) => (
        <div key={item.id} className={`toast ${item.kind}`}>
          {item.message}
        </div>
      ))}
    </div>
  );
}
