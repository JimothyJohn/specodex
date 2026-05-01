/**
 * AuthContext — login/register state, persisted tokens, auto-refresh.
 *
 * Sits parallel to AppContext (not nested inside it) so the two
 * concerns stay independent: auth shouldn't force a re-render of the
 * product cache, and AppContext doesn't need to know whether a user
 * is signed in.
 *
 * Token storage: localStorage under `specodex.auth.tokens`. This is
 * XSS-readable; CSP headers (Phase 5) close most of that gap. The
 * tradeoff is acknowledged in todo/AUTH.md — chosen over httpOnly
 * cookies because the backend runs cors({ origin: '*' }) and
 * cookie-based auth would require an explicit origin allowlist plus
 * SameSite decisions per environment.
 *
 * Auto-refresh: a single setTimeout schedules the next refresh ~60s
 * before the id token's `exp`. Refresh failure logs the user out;
 * proactive refresh avoids the 401-retry-after-refresh dance in the
 * request layer.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  ReactNode,
} from 'react';
import { apiClient } from '../api/client';
import { safeLoad, safeSave } from '../utils/localStorage';

const STORAGE_KEY = 'specodex.auth.tokens';
const REFRESH_BEFORE_EXP_MS = 60_000;

export interface AuthedUser {
  sub: string;
  email: string;
  groups: string[];
}

interface StoredTokens {
  idToken: string;
  accessToken: string;
  refreshToken: string;
}

const isStoredTokens = (v: unknown): v is StoredTokens =>
  !!v &&
  typeof v === 'object' &&
  typeof (v as StoredTokens).idToken === 'string' &&
  typeof (v as StoredTokens).accessToken === 'string' &&
  typeof (v as StoredTokens).refreshToken === 'string';

/**
 * Decode the payload of a JWT. Does NOT verify the signature — that's
 * the backend's job via aws-jwt-verify. Frontend just needs the
 * payload to schedule refreshes and surface email/groups.
 *
 * Cognito ID tokens are RFC 7519 JWTs; payload is base64url-encoded
 * JSON in the second segment.
 */
function decodeJwt(token: string): Record<string, unknown> | null {
  try {
    const segments = token.split('.');
    if (segments.length !== 3) return null;
    const padded = segments[1].replace(/-/g, '+').replace(/_/g, '/');
    const padding = padded.length % 4 === 0 ? '' : '='.repeat(4 - (padded.length % 4));
    const json = atob(padded + padding);
    return JSON.parse(json);
  } catch {
    return null;
  }
}

function userFromIdToken(idToken: string): AuthedUser | null {
  const payload = decodeJwt(idToken);
  if (!payload || typeof payload.sub !== 'string') return null;
  return {
    sub: payload.sub,
    email: typeof payload.email === 'string' ? payload.email : '',
    groups: Array.isArray(payload['cognito:groups'])
      ? (payload['cognito:groups'] as string[])
      : [],
  };
}

function expFromIdToken(idToken: string): number | null {
  const payload = decodeJwt(idToken);
  if (!payload || typeof payload.exp !== 'number') return null;
  return payload.exp;
}

interface AuthContextType {
  user: AuthedUser | null;
  isAdmin: boolean;
  loading: boolean;
  error: string | null;

  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<{ next: string }>;
  confirmSignup: (email: string, code: string) => Promise<void>;
  resendCode: (email: string) => Promise<void>;
  forgotPassword: (email: string) => Promise<void>;
  resetPassword: (email: string, code: string, password: string) => Promise<void>;
  logout: () => void;
  clearError: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [tokens, setTokens] = useState<StoredTokens | null>(() =>
    safeLoad(STORAGE_KEY, isStoredTokens, null as unknown as StoredTokens) || null,
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const user: AuthedUser | null = tokens ? userFromIdToken(tokens.idToken) : null;
  const isAdmin = !!user?.groups.includes('admin');

  // Push the current id token into the API client whenever it changes.
  // Single source of truth: tokens state here, request layer reads it
  // through setAuthToken.
  //
  // useLayoutEffect (not useEffect) so the singleton is updated before
  // any sibling/child useEffect runs — child effects fire BEFORE parent
  // effects in React, so a regular useEffect here lets a child like
  // ProjectsContext.refresh() race ahead with no token on first mount.
  // All useLayoutEffects run before any useEffect, regardless of depth.
  useLayoutEffect(() => {
    apiClient.setAuthToken(tokens?.idToken ?? null);
  }, [tokens]);

  // Persist tokens (or clear them) on every change. safeSave is
  // best-effort — a quota error doesn't break in-memory state.
  useEffect(() => {
    if (tokens) {
      safeSave(STORAGE_KEY, tokens);
    } else if (typeof window !== 'undefined') {
      try {
        window.localStorage.removeItem(STORAGE_KEY);
      } catch {
        // best-effort
      }
    }
  }, [tokens]);

  const logout = useCallback(() => {
    // Snapshot the refresh token before we clear local state — the
    // server-side revoke is fire-and-forget, but if we set tokens
    // null first we lose the value to send. apiClient.authLogout
    // already swallows errors, so a transient network blip can't
    // strand the user logged in client-side.
    const refreshToken = tokens?.refreshToken;
    if (refreshTimerRef.current) {
      clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
    setTokens(null);
    setError(null);
    if (refreshToken) {
      void apiClient.authLogout(refreshToken);
    }
  }, [tokens]);

  const refreshTokens = useCallback(async (refreshToken: string): Promise<void> => {
    try {
      const result = await apiClient.authRefresh(refreshToken);
      setTokens(prev => prev && {
        idToken: result.id_token,
        accessToken: result.access_token,
        refreshToken: prev.refreshToken,
      });
    } catch (err) {
      console.warn('[AuthContext] refresh failed, logging out:', err);
      // Don't surface this to the user — they're being silently logged
      // out, the next protected action prompts a re-login.
      logout();
    }
  }, [logout]);

  // Auto-refresh scheduler. Runs whenever the id token changes (login,
  // register-then-login, refresh). Schedules a single timer for
  // (exp - now) - 60s; if the token is already past that window,
  // refreshes immediately.
  useEffect(() => {
    if (refreshTimerRef.current) {
      clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
    if (!tokens) return;
    const exp = expFromIdToken(tokens.idToken);
    if (exp === null) {
      // Malformed token — drop it.
      logout();
      return;
    }
    const msUntilRefresh = exp * 1000 - Date.now() - REFRESH_BEFORE_EXP_MS;
    if (msUntilRefresh <= 0) {
      refreshTokens(tokens.refreshToken);
      return;
    }
    refreshTimerRef.current = setTimeout(() => {
      refreshTokens(tokens.refreshToken);
    }, msUntilRefresh);
    return () => {
      if (refreshTimerRef.current) {
        clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
    };
  }, [tokens, refreshTokens, logout]);

  const login = useCallback(async (email: string, password: string): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const result = await apiClient.authLogin(email, password);
      setTokens({
        idToken: result.id_token,
        accessToken: result.access_token,
        refreshToken: result.refresh_token,
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Login failed';
      setError(msg);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  const register = useCallback(async (email: string, password: string) => {
    setLoading(true);
    setError(null);
    try {
      return await apiClient.authRegister(email, password);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Registration failed';
      setError(msg);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  const confirmSignup = useCallback(async (email: string, code: string) => {
    setLoading(true);
    setError(null);
    try {
      await apiClient.authConfirm(email, code);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Verification failed';
      setError(msg);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  const resendCode = useCallback(async (email: string) => {
    setLoading(true);
    setError(null);
    try {
      await apiClient.authResendCode(email);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Resend failed';
      setError(msg);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  const forgotPassword = useCallback(async (email: string) => {
    setLoading(true);
    setError(null);
    try {
      await apiClient.authForgotPassword(email);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Reset request failed';
      setError(msg);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  const resetPassword = useCallback(async (email: string, code: string, password: string) => {
    setLoading(true);
    setError(null);
    try {
      await apiClient.authResetPassword(email, code, password);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Reset failed';
      setError(msg);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  const clearError = useCallback(() => setError(null), []);

  const value: AuthContextType = {
    user,
    isAdmin,
    loading,
    error,
    login,
    register,
    confirmSignup,
    resendCode,
    forgotPassword,
    resetPassword,
    logout,
    clearError,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext);
  if (ctx === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return ctx;
}
