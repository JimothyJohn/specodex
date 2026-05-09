/**
 * AuthModal — single modal hosting login / register / confirm /
 * forgot / reset flows.
 *
 * Why one modal instead of routes: the rest of the app is single-page
 * and the auth pages are short flows that benefit from sharing state
 * (email field carries across login → forgot → reset, register →
 * confirm). A modal keeps the user's place in the catalog instead of
 * navigating away mid-browse.
 *
 * Step flow:
 *   login  ↔ register
 *   login  → forgot → reset → login
 *   register → confirm → login
 */

import { FormEvent, useEffect, useRef, useState } from 'react';
import { useAuth } from '../context/AuthContext';

type Step = 'login' | 'register' | 'confirm' | 'forgot' | 'reset';

interface Props {
  open: boolean;
  initialStep?: Step;
  onClose: () => void;
}

// JS-side replacement for UA validation bubbles. STYLE.md Phase 4 takes
// `noValidate` on every form so the OS-styled "Please fill out this
// field" tooltip never appears; we mirror what the UA was checking
// (required + email format + minLength) and surface a themed error in
// the existing `.auth-modal-error` slot.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function validateAuthForm(step: Step, email: string, password: string, code: string): string | null {
  if (!email.trim()) return 'Email is required.';
  if (!EMAIL_RE.test(email.trim())) return 'Enter a valid email address.';
  if ((step === 'login' || step === 'register' || step === 'reset')) {
    if (!password) return 'Password is required.';
    if ((step === 'register' || step === 'reset') && password.length < 12) {
      return 'Password must be at least 12 characters.';
    }
  }
  if ((step === 'confirm' || step === 'reset') && !code.trim()) {
    return step === 'reset' ? 'Reset code is required.' : 'Verification code is required.';
  }
  return null;
}

export default function AuthModal({ open, initialStep = 'login', onClose }: Props) {
  const auth = useAuth();
  const [step, setStep] = useState<Step>(initialStep);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  // Client-side validation error. Cleared on step switch + on submit.
  // Distinct from `auth.error`, which carries server-side errors.
  const [validationError, setValidationError] = useState<string | null>(null);
  const modalRef = useRef<HTMLDivElement>(null);

  // useAuth returns a fresh context value on every parent render. If
  // we depended on `auth` directly, this effect would fire after every
  // login attempt and erase the just-set error. The ref lets us call
  // the latest `clearError` only when open/initialStep actually change.
  const clearErrorRef = useRef(auth.clearError);
  clearErrorRef.current = auth.clearError;

  useEffect(() => {
    if (open) {
      setStep(initialStep);
      setStatusMsg(null);
      clearErrorRef.current();
    }
  }, [open, initialStep]);

  useEffect(() => {
    if (!open) return;
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    const onClick = (e: MouseEvent) => {
      if (modalRef.current && !modalRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener('keydown', onEsc);
    document.addEventListener('mousedown', onClick);
    return () => {
      document.removeEventListener('keydown', onEsc);
      document.removeEventListener('mousedown', onClick);
    };
  }, [open, onClose]);

  if (!open) return null;

  function switchTo(next: Step) {
    auth.clearError();
    setStatusMsg(null);
    setValidationError(null);
    setStep(next);
  }

  // Run JS validation up front and short-circuit on failure. Returns
  // true when ready to submit, false when a validationError was set
  // (the existing `.auth-modal-error` slot renders it).
  function preflight(formStep: Step): boolean {
    const err = validateAuthForm(formStep, email, password, code);
    setValidationError(err);
    if (err) auth.clearError();
    return err === null;
  }

  async function onLogin(e: FormEvent) {
    e.preventDefault();
    setStatusMsg(null);
    if (!preflight('login')) return;
    try {
      await auth.login(email, password);
      onClose();
    } catch {
      // error already in auth.error
    }
  }

  async function onRegister(e: FormEvent) {
    e.preventDefault();
    setStatusMsg(null);
    if (!preflight('register')) return;
    try {
      await auth.register(email, password);
      setStatusMsg('Check your email for a verification code.');
      setStep('confirm');
    } catch {
      // error already in auth.error
    }
  }

  async function onConfirm(e: FormEvent) {
    e.preventDefault();
    setStatusMsg(null);
    if (!preflight('confirm')) return;
    try {
      await auth.confirmSignup(email, code);
      setStatusMsg('Email verified — you can sign in now.');
      setCode('');
      setStep('login');
    } catch {
      // error already in auth.error
    }
  }

  async function onResend() {
    setStatusMsg(null);
    try {
      await auth.resendCode(email);
      setStatusMsg('Verification code resent.');
    } catch {
      // error already in auth.error
    }
  }

  async function onForgot(e: FormEvent) {
    e.preventDefault();
    setStatusMsg(null);
    if (!preflight('forgot')) return;
    try {
      await auth.forgotPassword(email);
      setStatusMsg('If an account exists for that email, a reset code is on the way.');
      setStep('reset');
    } catch {
      // error already in auth.error
    }
  }

  async function onReset(e: FormEvent) {
    e.preventDefault();
    setStatusMsg(null);
    if (!preflight('reset')) return;
    try {
      await auth.resetPassword(email, code, password);
      setStatusMsg('Password reset — you can sign in now.');
      setCode('');
      setPassword('');
      setStep('login');
    } catch {
      // error already in auth.error
    }
  }

  const headerByStep: Record<Step, string> = {
    login: 'Sign in',
    register: 'Create account',
    confirm: 'Verify email',
    forgot: 'Forgot password',
    reset: 'Reset password',
  };

  return (
    <div className="auth-modal-overlay" role="dialog" aria-modal="true" aria-label={headerByStep[step]}>
      <div ref={modalRef} className="auth-modal">
        <div className="auth-modal-header">
          <h2>{headerByStep[step]}</h2>
          <button type="button" className="auth-modal-close" onClick={onClose} aria-label="Close">×</button>
        </div>

        {/* Validation errors take precedence — once the server responds
         *  it sets auth.error, which is more informative than the
         *  client-side check that already passed. */}
        {(validationError || auth.error) && (
          <div className="auth-modal-error" role="alert">{validationError ?? auth.error}</div>
        )}
        {statusMsg && <div className="auth-modal-status" role="status">{statusMsg}</div>}

        {step === 'login' && (
          <form noValidate onSubmit={onLogin} className="auth-form">
            <label>
              Email
              <input
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={e => setEmail(e.target.value)}
              />
            </label>
            <label>
              Password
              <input
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={e => setPassword(e.target.value)}
              />
            </label>
            <button type="submit" disabled={auth.loading} className="auth-form-submit">
              {auth.loading ? 'Signing in…' : 'Sign in'}
            </button>
            <div className="auth-form-links">
              <button type="button" className="auth-form-link" onClick={() => switchTo('forgot')}>
                Forgot password?
              </button>
              <button type="button" className="auth-form-link" onClick={() => switchTo('register')}>
                Create account
              </button>
            </div>
          </form>
        )}

        {step === 'register' && (
          <form noValidate onSubmit={onRegister} className="auth-form">
            <label>
              Email
              <input
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={e => setEmail(e.target.value)}
              />
            </label>
            <label>
              Password
              <input
                type="password"
                autoComplete="new-password"
                required
                minLength={12}
                value={password}
                onChange={e => setPassword(e.target.value)}
              />
            </label>
            <small className="auth-form-hint">12+ characters, mixed case and a number.</small>
            <button type="submit" disabled={auth.loading} className="auth-form-submit">
              {auth.loading ? 'Creating account…' : 'Create account'}
            </button>
            <div className="auth-form-links">
              <button type="button" className="auth-form-link" onClick={() => switchTo('login')}>
                Already have an account? Sign in
              </button>
            </div>
          </form>
        )}

        {step === 'confirm' && (
          <form noValidate onSubmit={onConfirm} className="auth-form">
            <label>
              Email
              <input
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={e => setEmail(e.target.value)}
              />
            </label>
            <label>
              Verification code
              <input
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                required
                value={code}
                onChange={e => setCode(e.target.value)}
              />
            </label>
            <button type="submit" disabled={auth.loading} className="auth-form-submit">
              {auth.loading ? 'Verifying…' : 'Verify'}
            </button>
            <div className="auth-form-links">
              <button type="button" className="auth-form-link" onClick={onResend}>
                Resend code
              </button>
              <button type="button" className="auth-form-link" onClick={() => switchTo('login')}>
                Back to sign in
              </button>
            </div>
          </form>
        )}

        {step === 'forgot' && (
          <form noValidate onSubmit={onForgot} className="auth-form">
            <label>
              Email
              <input
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={e => setEmail(e.target.value)}
              />
            </label>
            <button type="submit" disabled={auth.loading} className="auth-form-submit">
              {auth.loading ? 'Sending…' : 'Send reset code'}
            </button>
            <div className="auth-form-links">
              <button type="button" className="auth-form-link" onClick={() => switchTo('login')}>
                Back to sign in
              </button>
            </div>
          </form>
        )}

        {step === 'reset' && (
          <form noValidate onSubmit={onReset} className="auth-form">
            <label>
              Email
              <input
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={e => setEmail(e.target.value)}
              />
            </label>
            <label>
              Reset code
              <input
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                required
                value={code}
                onChange={e => setCode(e.target.value)}
              />
            </label>
            <label>
              New password
              <input
                type="password"
                autoComplete="new-password"
                required
                minLength={12}
                value={password}
                onChange={e => setPassword(e.target.value)}
              />
            </label>
            <small className="auth-form-hint">12+ characters, mixed case and a number.</small>
            <button type="submit" disabled={auth.loading} className="auth-form-submit">
              {auth.loading ? 'Resetting…' : 'Reset password'}
            </button>
            <div className="auth-form-links">
              <button type="button" className="auth-form-link" onClick={() => switchTo('login')}>
                Back to sign in
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
