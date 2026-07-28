/**
 * Background: AsterMem backend is FastAPI with ~85 REST endpoints, cookie-session auth,
 * returning 401 for unauthenticated requests; the frontend uses relative paths /api/....
 * Design intent: A unified fetch wrapper that handles three concerns: global 401 redirect
 * to /login, JSON error message extraction (tolerant of detail/message/error fields),
 * and normalizing network exceptions into ApiError.
 * Callers decide whether to toast errors (most use reportError as a catch-all).
 * Key constraint: Backend fields may be missing—page layer must treat return values as optional;
 * this module performs no business field validation, only guarantees "get JSON or throw ApiError".
 */
import { emitToast } from "./toast";
import { translateStandalone } from "./i18n";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/**
 * Background: Any business API returns 401 when not logged in; requires a site-wide redirect to login.
 * Design intent: Remember the current path before redirecting so we can return after login;
 * do not trigger redirect on /login itself or when calling auth endpoints, to avoid loops.
 * Key constraint: Uses window.location hard redirect to ensure all in-memory state is cleared.
 */
function redirectToLogin() {
  if (window.location.pathname === "/login") return;
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.href = `/login?next=${next}`;
}

/**
 * Background: FastAPI error bodies come in various shapes: {detail}, {detail:{msg}}, {message}, {error}.
 * Design intent: Best-effort extraction of a human-readable message, falling back to HTTP status line.
 * Key constraint: Never throws a secondary exception.
 */
async function extractErrorMessage(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data?.detail === "string") return data.detail;
    if (typeof data?.detail?.msg === "string") return data.detail.msg;
    if (Array.isArray(data?.detail) && data.detail[0]?.msg) return String(data.detail[0].msg);
    if (typeof data?.message === "string") return data.message;
    if (typeof data?.error === "string") return data.error;
  } catch {
    // Response body is not JSON (e.g. gateway 502 HTML page), fall back to status line.
  }
  return `HTTP ${res.status}`;
}

async function handleResponse<T>(res: Response, skipAuthRedirect: boolean): Promise<T> {
  if (res.status === 401 && !skipAuthRedirect) {
    redirectToLogin();
    throw new ApiError(401, "Unauthorized");
  }
  if (!res.ok) {
    throw new ApiError(res.status, await extractErrorMessage(res));
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(res.status, "Invalid JSON response");
  }
}

interface RequestOptions {
  /** Auth-check endpoints need the raw 401 instead of being redirected */
  skipAuthRedirect?: boolean;
}

/**
 * Background: The single entry point for all JSON API requests.
 * Design intent: Network-layer exceptions (disconnected, DNS) are wrapped as status=0 ApiError,
 * so page-layer code can handle them with the same catch logic.
 * Key constraint: When body is undefined, Content-Type is not sent, compatible with GET/DELETE.
 */
export async function api<T = unknown>(
  method: string,
  path: string,
  body?: unknown,
  options: RequestOptions = {},
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    throw new ApiError(0, err instanceof Error ? err.message : "Network error");
  }
  return handleResponse<T>(res, options.skipAuthRedirect ?? false);
}

/**
 * Background: Zip/json import and image upload use multipart upload, cannot reuse the JSON entry.
 * Design intent: Separate FormData request wrapper with the same error path as api().
 * Key constraint: Do not manually set Content-Type—let the browser include the boundary.
 */
export async function apiUpload<T = unknown>(path: string, form: FormData): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { method: "POST", credentials: "same-origin", body: form });
  } catch (err) {
    throw new ApiError(0, err instanceof Error ? err.message : "Network error");
  }
  return handleResponse<T>(res, false);
}

/**
 * Background: POST /api/export returns a zip binary that needs to trigger a browser download.
 * Design intent: Blob + temporary <a> element for download, extracting filename from
 * Content-Disposition, falling back to a date-based default name.
 * Key constraint: URL.revokeObjectURL must be called to avoid memory leaks.
 */
export async function apiDownload(path: string, fallbackName: string): Promise<void> {
  let res: Response;
  try {
    res = await fetch(path, { method: "POST", credentials: "same-origin" });
  } catch (err) {
    throw new ApiError(0, err instanceof Error ? err.message : "Network error");
  }
  if (res.status === 401) {
    redirectToLogin();
    throw new ApiError(401, "Unauthorized");
  }
  if (!res.ok) throw new ApiError(res.status, await extractErrorMessage(res));
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") || "";
  const match = /filename="?([^";]+)"?/.exec(disposition);
  const name = match?.[1] || fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export interface SseHandlers {
  /** Called for each parsed data: payload line; payload may be a JSON object or raw string */
  onData: (payload: unknown) => void;
  onDone?: () => void;
  onError?: (err: ApiError) => void;
}

/**
 * Background: The AI explore endpoints (/api/explore/*) use SSE streaming;
 * EventSource doesn't support POST, so we use fetch + ReadableStream with manual parsing.
 * Design intent: Split the buffer by lines, only process data: prefixed lines;
 * [DONE] sentinel triggers onDone; data payload is parsed as JSON first,
 * falling back to passing the raw string to the caller, ensuring any backend format works.
 * Key constraint: Returns an abort function that must be called on page unmount or new query.
 */
export function sseStream(path: string, body: unknown, handlers: SseHandlers): () => void {
  const controller = new AbortController();

  (async () => {
    let res: Response;
    try {
      res = await fetch(path, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (err) {
      if (controller.signal.aborted) return;
      handlers.onError?.(new ApiError(0, err instanceof Error ? err.message : "Network error"));
      return;
    }
    if (res.status === 401) {
      redirectToLogin();
      return;
    }
    if (!res.ok || !res.body) {
      handlers.onError?.(new ApiError(res.status, await extractErrorMessage(res)));
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const rawLine of lines) {
          const line = rawLine.replace(/\r$/, "");
          if (!line.startsWith("data:")) continue;
          const data = line.slice(5).trim();
          if (!data) continue;
          if (data === "[DONE]") {
            handlers.onDone?.();
            return;
          }
          try {
            handlers.onData(JSON.parse(data));
          } catch {
            // Non-JSON payload passed through as plain text; page layer decides how to display.
            handlers.onData(data);
          }
        }
      }
      handlers.onDone?.();
    } catch (err) {
      if (!controller.signal.aborted) {
        handlers.onError?.(new ApiError(0, err instanceof Error ? err.message : "Stream error"));
      }
    }
  })();

  return () => controller.abort();
}

/**
 * Background: Many catch branches in pages just "toast an error", needing a unified outlet.
 * Design intent: Display ApiError message directly (backend detail is usually human-readable),
 * show fallback text for unknown exceptions; console.error preserves the full stack for debugging.
 * Key constraint: Fallback text is pre-translated by the caller.
 */
export function reportError(err: unknown, fallback: string) {
  console.error("[AsterMem]", err);
  if (err instanceof ApiError && err.status === 401) return; // Already redirecting to login, no toast needed
  const message = err instanceof ApiError && err.message
    ? translateStandalone(err.message)
    : fallback;
  emitToast("error", message);
}
