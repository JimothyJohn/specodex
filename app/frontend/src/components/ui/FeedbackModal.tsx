/**
 * FeedbackModal — app-native form for "missing product / wrong info /
 * can't find what I need / general" feedback.
 *
 * No backend: submit composes a `mailto:nick@advin.io` URL via
 * `buildFeedbackMailto` and opens it in the user's mail client. See
 * utils/feedback.ts for the rationale.
 *
 * Style note: deliberately mirrors ConfirmDialog's overlay/dialog
 * structure so it reads as part of the same family. No <dialog>, no
 * window.alert (per todo/STYLE.md drift gates).
 *
 * Usage:
 *   <FeedbackModal
 *     open={open}
 *     onClose={() => setOpen(false)}
 *     defaultCategory="no_match"
 *     context={{ productType, filters, route: location.pathname }}
 *   />
 */

import { FormEvent, useEffect, useRef, useState } from 'react';
import './FeedbackModal.css';
import {
  buildFeedbackMailto,
  FeedbackCategory,
  FeedbackContext,
  FEEDBACK_CATEGORY_LABELS,
} from '../../utils/feedback';

interface Props {
  open: boolean;
  onClose: () => void;
  /** Pre-select a category — useful for context-specific entry points. */
  defaultCategory?: FeedbackCategory;
  /** Captured app context auto-appended to the mail body. */
  context?: FeedbackContext;
  /** Optional override for the heading copy. */
  title?: string;
}

const CATEGORY_ORDER: FeedbackCategory[] = [
  'missing_product',
  'wrong_info',
  'no_match',
  'general',
];

export default function FeedbackModal({
  open,
  onClose,
  defaultCategory = 'general',
  context,
  title = 'Send feedback',
}: Props) {
  const [category, setCategory] = useState<FeedbackCategory>(defaultCategory);
  const [message, setMessage] = useState('');
  const dialogRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // Capture the trigger element so we can return focus on close — same
  // pattern as ConfirmDialog.
  const triggerElRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    triggerElRef.current = (document.activeElement as HTMLElement) || null;
    // Reset to defaults each open so a stale message doesn't bleed across
    // two distinct feedback events.
    setCategory(defaultCategory);
    setMessage('');
    // Defer focus to give the dialog a frame to mount.
    const id = window.setTimeout(() => textareaRef.current?.focus(), 0);
    return () => {
      window.clearTimeout(id);
      triggerElRef.current?.focus?.();
    };
  }, [open, defaultCategory]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const href = buildFeedbackMailto({
      category,
      message,
      context: {
        ...context,
        // Default route to the current pathname/search so the helper
        // doesn't require every call site to thread it manually.
        route:
          context?.route ??
          (typeof window !== 'undefined'
            ? window.location.pathname + window.location.search
            : undefined),
      },
    });
    // Same-tab mail-client launch. Anchor target=_self is the default
    // behaviour, but spelling it out keeps tests deterministic.
    if (typeof window !== 'undefined') {
      window.location.href = href;
    }
    onClose();
  }

  return (
    <div
      className="confirm-dialog-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="feedback-modal-title"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className="confirm-dialog feedback-modal"
        // noValidate per todo/STYLE.md — UA validation bubbles are
        // banned, JS handles validation inline.
      >
        <h2 id="feedback-modal-title" className="confirm-dialog-title">
          {title}
        </h2>
        <form onSubmit={onSubmit} noValidate>
          <fieldset className="feedback-modal-fieldset">
            <legend className="feedback-modal-legend">What kind of feedback?</legend>
            {CATEGORY_ORDER.map((c) => (
              <label key={c} className="feedback-modal-radio">
                <input
                  type="radio"
                  name="feedback-category"
                  value={c}
                  checked={category === c}
                  onChange={() => setCategory(c)}
                />
                <span>{FEEDBACK_CATEGORY_LABELS[c]}</span>
              </label>
            ))}
          </fieldset>

          <label className="feedback-modal-message-label" htmlFor="feedback-modal-message">
            Tell us what's going on
          </label>
          <textarea
            ref={textareaRef}
            id="feedback-modal-message"
            className="feedback-modal-textarea"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            rows={6}
            placeholder="Optional. The more specific, the easier it is to act on."
          />

          <p className="feedback-modal-note">
            Submitting opens your email client with a pre-filled message to{' '}
            <strong>nick@advin.io</strong>. App context (current page, product
            type, active filters) is included to help triage.
          </p>

          <div className="confirm-dialog-actions">
            <button
              type="button"
              className="confirm-dialog-cancel"
              data-confirm-role="cancel"
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="confirm-dialog-confirm confirm-dialog-confirm--default"
              data-confirm-role="confirm"
            >
              Compose email
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
