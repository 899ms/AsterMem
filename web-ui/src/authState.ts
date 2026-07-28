/**
 * Background: AuthGate already calls /api/auth/check on mount; the Layout sidebar also needs
 * the same result—footer shows the current account, and "Sign out" should be hidden when login
 * protection is off (no session to sign out of).
 * Design intent: A tiny module-level snapshot + subscription shares the probe result,
 * avoiding redundant requests on every page; the admin page publishes once after changing
 * account or toggling the switch so the sidebar updates immediately.
 * Key constraint: Only caches display state (account name & login_required), never credentials;
 * defaults to "login required" when the probe result is unavailable.
 */
import { useEffect, useState } from "react";

export interface AuthSnapshot {
  loginRequired: boolean;
  username: string;
  /** Public read-only showcase: the backend rejects writes, so the UI hides the controls for them. */
  demoMode: boolean;
}

let snapshot: AuthSnapshot = { loginRequired: true, username: "", demoMode: false };
const listeners = new Set<(value: AuthSnapshot) => void>();

export function publishAuthSnapshot(patch: Partial<AuthSnapshot>) {
  snapshot = { ...snapshot, ...patch };
  listeners.forEach((listener) => listener(snapshot));
}

export function useAuthSnapshot(): AuthSnapshot {
  const [value, setValue] = useState(snapshot);
  useEffect(() => {
    listeners.add(setValue);
    setValue(snapshot);
    return () => {
      listeners.delete(setValue);
    };
  }, []);
  return value;
}
