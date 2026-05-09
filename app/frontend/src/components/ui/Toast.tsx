/**
 * Toast — themed transient notifications (replaces silent console.error).
 *
 * STYLE.md Phase 3. Async API failures used to surface only as
 * console.error in DevTools while the optimistic UI silently reverted —
 * the user had no idea anything went wrong. This primitive makes
 * failures (and the occasional success) visible *inside* the app.
 *
 * Usage:
 *   const toast = useToast();
 *   toast.error("Couldn't update product", { detail: err.message });
 *   toast.success("Copied BOM to clipboard");
 *   toast.info("Refreshing categories…");
 *
 * Mount `<ToastProvider>` once near the top of the React tree, outside
 * any provider whose mutators want to call `useToast()` (App.tsx wraps
 * AppProvider in ToastProvider for exactly this reason).
 *
 * Implementation: a context-backed list of pending toasts, rendered
 * into a portal at the bottom-right of the viewport. Auto-dismiss
 * timers run per-toast (5s for success/info, 8s for error so the user
 * has time to read the failure). The list is capped at MAX_VISIBLE; new
 * toasts past the cap evict the oldest.
 */

import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import './Toast.css';
import { createPortal } from 'react-dom';

export type ToastVariant = 'success' | 'error' | 'info';

export interface ToastOptions {
  /** Secondary line under the headline — error message, BOM count, etc. */
  detail?: string;
  /** Override auto-dismiss; pass 0 to disable auto-dismiss entirely. */
  durationMs?: number;
}

export interface ToastApi {
  success: (message: string, opts?: ToastOptions) => void;
  error: (message: string, opts?: ToastOptions) => void;
  info: (message: string, opts?: ToastOptions) => void;
  /** Dismiss a toast by id (returned implicitly to consumers via push). */
  dismiss: (id: string) => void;
}

interface ToastEntry {
  id: string;
  message: string;
  variant: ToastVariant;
  detail?: string;
  durationMs: number;
}

const MAX_VISIBLE = 4;
const DEFAULT_DURATION: Record<ToastVariant, number> = {
  success: 5000,
  info: 5000,
  // Errors stick longer — the user has to read the failure and decide
  // whether to retry. 8s matches the spec in todo/STYLE.md.
  error: 8000,
};

const ToastContext = createContext<ToastApi | null>(null);

let _idSeq = 0;
const _nextId = (): string => `toast-${++_idSeq}-${Date.now().toString(36)}`;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastEntry[]>([]);
  // Hold timer ids so we can clear them on dismiss / unmount and avoid
  // dispatching state updates on stale toasts.
  const timersRef = useRef<Map<string, number>>(new Map());

  const dismiss = useCallback((id: string) => {
    const timer = timersRef.current.get(id);
    if (timer !== undefined) {
      window.clearTimeout(timer);
      timersRef.current.delete(id);
    }
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (variant: ToastVariant, message: string, opts?: ToastOptions) => {
      const id = _nextId();
      const durationMs = opts?.durationMs ?? DEFAULT_DURATION[variant];
      setToasts((prev) => {
        const next = [...prev, { id, message, variant, detail: opts?.detail, durationMs }];
        // Cap stack — drop oldest if we'd exceed MAX_VISIBLE.
        if (next.length > MAX_VISIBLE) {
          const evicted = next.slice(0, next.length - MAX_VISIBLE);
          for (const t of evicted) {
            const timer = timersRef.current.get(t.id);
            if (timer !== undefined) {
              window.clearTimeout(timer);
              timersRef.current.delete(t.id);
            }
          }
          return next.slice(-MAX_VISIBLE);
        }
        return next;
      });
      if (durationMs > 0) {
        const timer = window.setTimeout(() => dismiss(id), durationMs);
        timersRef.current.set(id, timer);
      }
    },
    [dismiss],
  );

  // Clear pending timers on unmount so they don't fire after the
  // provider is gone (would warn "state update on unmounted component").
  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      for (const t of timers.values()) window.clearTimeout(t);
      timers.clear();
    };
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      success: (m, o) => push('success', m, o),
      error: (m, o) => push('error', m, o),
      info: (m, o) => push('info', m, o),
      dismiss,
    }),
    [push, dismiss],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <ToastRegion toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

// No-op fallback used when the hook is called outside ToastProvider.
// Toasts are a UI side effect, not critical state — missing provider
// gracefully degrades to "no toast shown" instead of a hard error.
// This keeps tests that mount internal providers (AppProvider directly,
// without an outer ToastProvider) working without requiring every test
// to wrap with a ToastProvider it doesn't actually exercise.
const _noop: ToastApi = {
  success: () => {},
  error: () => {},
  info: () => {},
  dismiss: () => {},
};

/**
 * Hook returning the imperative toast API. The returned object is
 * stable across renders, safe to drop into useEffect / useCallback deps.
 *
 * If called outside a `<ToastProvider>` (most commonly in unit tests),
 * returns a no-op API rather than throwing — toasts are non-essential
 * UI side effects, so missing the provider should degrade silently.
 */
export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  return ctx ?? _noop;
}

interface ToastRegionProps {
  toasts: ToastEntry[];
  onDismiss: (id: string) => void;
}

function ToastRegion({ toasts, onDismiss }: ToastRegionProps) {
  if (typeof document === 'undefined' || toasts.length === 0) return null;
  return createPortal(
    <div
      className="toast-region"
      // aria-live region — screen readers announce additions. `polite`
      // matches the visual non-blocking nature of the toast.
      aria-live="polite"
      aria-relevant="additions"
    >
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={onDismiss} />
      ))}
    </div>,
    document.body,
  );
}

interface ToastItemProps {
  toast: ToastEntry;
  onDismiss: (id: string) => void;
}

function ToastItem({ toast, onDismiss }: ToastItemProps) {
  // role=alert for errors so screen readers interrupt; role=status for
  // success/info so they announce without interrupting.
  const role = toast.variant === 'error' ? 'alert' : 'status';
  return (
    <div className={`toast toast--${toast.variant}`} role={role}>
      <div className="toast__body">
        <div className="toast__message">{toast.message}</div>
        {toast.detail && <div className="toast__detail">{toast.detail}</div>}
      </div>
      <button
        type="button"
        className="toast__close"
        aria-label="Dismiss notification"
        onClick={() => onDismiss(toast.id)}
      >
        ×
      </button>
    </div>
  );
}

export default ToastProvider;
