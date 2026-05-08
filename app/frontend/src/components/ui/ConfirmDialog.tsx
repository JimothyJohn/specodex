/**
 * ConfirmDialog — themed replacement for `window.confirm()`.
 *
 * STYLE.md Phase 2. Native `confirm()` blocks the JS thread, renders
 * in OS chrome, can't be themed, and traps the user in a synchronous
 * dialog the test runner can't dismiss. This primitive is async, themed,
 * keyboard-friendly (Esc cancels, Enter confirms), and lets tests
 * resolve the dialog programmatically by clicking the rendered buttons.
 *
 * Usage:
 *   const confirm = useConfirm();
 *   if (!(await confirm({
 *     title: 'Delete project?',
 *     body: 'This removes the project and all its product references.',
 *     confirmLabel: 'Delete',
 *     confirmVariant: 'danger',
 *   }))) return;
 *
 * Mount `<ConfirmProvider>` once near the top of the tree (App.tsx).
 *
 * Implementation: the provider holds at most one pending confirm and
 * its resolver. Calling `useConfirm()(opts)` sets the pending confirm
 * and returns a Promise that resolves when the user clicks Confirm
 * (true), Cancel (false), or dismisses via Esc / backdrop / close X
 * (false). A second call while another is pending resolves the older
 * one with `false` first — same UX as if the user had cancelled it.
 */

import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react';

export type ConfirmVariant = 'default' | 'danger';

export interface ConfirmOptions {
  /** Modal heading. Required. */
  title: string;
  /** Body text — short paragraph or ReactNode. */
  body?: ReactNode;
  /** Label for the confirming action. Defaults to 'OK'. */
  confirmLabel?: string;
  /** Label for the cancelling action. Defaults to 'Cancel'. */
  cancelLabel?: string;
  /** Visual variant for the confirm button. */
  confirmVariant?: ConfirmVariant;
}

type Resolver = (result: boolean) => void;

interface PendingConfirm {
  options: ConfirmOptions;
  resolve: Resolver;
}

const ConfirmContext = createContext<((opts: ConfirmOptions) => Promise<boolean>) | null>(null);

/**
 * Provider that owns the pending confirm and renders the modal.
 * Mount once near the top of the React tree.
 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  // Capture the resolver in a ref so the modal's button handlers can
  // resolve even after a re-render replaces `pending`.
  const pendingRef = useRef<PendingConfirm | null>(null);
  pendingRef.current = pending;

  const confirm = useCallback((opts: ConfirmOptions): Promise<boolean> => {
    return new Promise<boolean>((resolve) => {
      // If another confirm is already open, cancel it first — there's only
      // one dialog slot. Resolving with `false` matches what the user would
      // have done by hitting Esc.
      if (pendingRef.current) {
        pendingRef.current.resolve(false);
      }
      setPending({ options: opts, resolve });
    });
  }, []);

  const handleResolve = useCallback((result: boolean) => {
    const current = pendingRef.current;
    if (!current) return;
    current.resolve(result);
    setPending(null);
  }, []);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {pending && (
        <ConfirmDialog
          options={pending.options}
          onConfirm={() => handleResolve(true)}
          onCancel={() => handleResolve(false)}
        />
      )}
    </ConfirmContext.Provider>
  );
}

/**
 * Returns an imperative `confirm` function. The returned function is
 * stable across renders (memoised by the provider), so it's safe to
 * include in `useEffect` / `useCallback` deps without re-firing.
 */
export function useConfirm(): (opts: ConfirmOptions) => Promise<boolean> {
  const ctx = useContext(ConfirmContext);
  if (!ctx) {
    throw new Error('useConfirm must be used within a <ConfirmProvider>.');
  }
  return ctx;
}

interface ConfirmDialogProps {
  options: ConfirmOptions;
  onConfirm: () => void;
  onCancel: () => void;
}

function ConfirmDialog({ options, onConfirm, onCancel }: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const confirmBtnRef = useRef<HTMLButtonElement>(null);
  // Capture the element that had focus before the dialog opened so we
  // can return focus there on close — same UX as the auth modal.
  const triggerElRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    triggerElRef.current = (document.activeElement as HTMLElement) || null;
    // Focus the confirm button by default. For 'danger' the user is
    // committing to a destructive action and benefits from a clear
    // affordance; either way, hitting Enter is the most common path.
    confirmBtnRef.current?.focus();
    return () => {
      // Return focus to the trigger when the dialog closes.
      triggerElRef.current?.focus?.();
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onCancel();
      } else if (e.key === 'Enter') {
        // Only intercept Enter if focus isn't on the cancel button.
        const active = document.activeElement;
        if (active instanceof HTMLButtonElement && active.dataset.confirmRole === 'cancel') {
          return;
        }
        e.preventDefault();
        onConfirm();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onConfirm, onCancel]);

  const variant = options.confirmVariant ?? 'default';

  return (
    <div
      className="confirm-dialog-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      onMouseDown={(e) => {
        // Click on the backdrop (not inside the dialog itself) cancels.
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div ref={dialogRef} className="confirm-dialog">
        <h2 id="confirm-dialog-title" className="confirm-dialog-title">
          {options.title}
        </h2>
        {options.body && <div className="confirm-dialog-body">{options.body}</div>}
        <div className="confirm-dialog-actions">
          <button
            type="button"
            className="confirm-dialog-cancel"
            data-confirm-role="cancel"
            onClick={onCancel}
          >
            {options.cancelLabel ?? 'Cancel'}
          </button>
          <button
            ref={confirmBtnRef}
            type="button"
            className={`confirm-dialog-confirm confirm-dialog-confirm--${variant}`}
            data-confirm-role="confirm"
            onClick={onConfirm}
          >
            {options.confirmLabel ?? 'OK'}
          </button>
        </div>
      </div>
    </div>
  );
}
