/**
 * Tests for AuthContext: token persistence, login flow, auto-refresh,
 * and logout. The api/client module is mocked at the module
 * boundary; we exercise the context state machine without going to
 * the network.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, render, renderHook, waitFor } from '@testing-library/react';
import { ReactNode } from 'react';
import { AuthProvider, useAuth } from './AuthContext';

vi.mock('../api/client', () => ({
  apiClient: {
    setAuthToken: vi.fn(),
    authLogin: vi.fn(),
    authRegister: vi.fn(),
    authConfirm: vi.fn(),
    authResendCode: vi.fn(),
    authForgotPassword: vi.fn(),
    authResetPassword: vi.fn(),
    authRefresh: vi.fn(),
    // Resolves immediately by default; the production implementation
    // already swallows errors, so a never-throwing mock matches the
    // observable contract.
    authLogout: vi.fn().mockResolvedValue(undefined),
  },
}));

import { apiClient } from '../api/client';

// Build a Cognito-shaped JWT (unsigned — we don't verify on the
// frontend; AuthContext only base64-decodes the payload).
function fakeIdToken(claims: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'RS256', typ: 'JWT' }))
    .replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
  const payload = btoa(JSON.stringify(claims))
    .replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
  return `${header}.${payload}.sig`;
}

const wrapper = ({ children }: { children: ReactNode }) => <AuthProvider>{children}</AuthProvider>;

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('AuthContext initial state', () => {
  it('starts logged out when localStorage is empty', () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.user).toBeNull();
    expect(result.current.isAdmin).toBe(false);
  });

  it('hydrates from localStorage on mount', () => {
    const idToken = fakeIdToken({
      sub: 'u-1',
      email: 'a@example.com',
      'cognito:groups': ['admin'],
      exp: Math.floor(Date.now() / 1000) + 3600,
    });
    localStorage.setItem('specodex.auth.tokens', JSON.stringify({
      idToken, accessToken: 'a', refreshToken: 'r',
    }));

    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.user?.email).toBe('a@example.com');
    expect(result.current.isAdmin).toBe(true);
    expect(apiClient.setAuthToken).toHaveBeenCalledWith(idToken);
  });

  it('ignores malformed localStorage payload', () => {
    localStorage.setItem('specodex.auth.tokens', 'not-json');
    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.user).toBeNull();
  });
});

describe('login', () => {
  it('on success: updates user state, persists to localStorage, pushes token to api client', async () => {
    const idToken = fakeIdToken({
      sub: 'u-2',
      email: 'b@example.com',
      exp: Math.floor(Date.now() / 1000) + 3600,
    });
    (apiClient.authLogin as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      id_token: idToken,
      access_token: 'access',
      refresh_token: 'refresh',
      expires_in: 3600,
    });

    const { result } = renderHook(() => useAuth(), { wrapper });
    await act(async () => {
      await result.current.login('b@example.com', 'pw');
    });

    expect(result.current.user?.sub).toBe('u-2');
    expect(localStorage.getItem('specodex.auth.tokens')).toContain(idToken);
    expect(apiClient.setAuthToken).toHaveBeenCalledWith(idToken);
  });

  it('on failure: surfaces error, no user set', async () => {
    (apiClient.authLogin as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error('Invalid credentials'));

    const { result } = renderHook(() => useAuth(), { wrapper });
    await act(async () => {
      try {
        await result.current.login('b@example.com', 'wrong');
      } catch {
        /* expected */
      }
    });

    expect(result.current.user).toBeNull();
    expect(result.current.error).toBe('Invalid credentials');
  });
});

describe('logout', () => {
  it('clears user state, localStorage, and api client token', async () => {
    const idToken = fakeIdToken({
      sub: 'u-3',
      email: 'c@example.com',
      exp: Math.floor(Date.now() / 1000) + 3600,
    });
    localStorage.setItem('specodex.auth.tokens', JSON.stringify({
      idToken, accessToken: 'a', refreshToken: 'r',
    }));

    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.user).not.toBeNull();

    act(() => { result.current.logout(); });

    expect(result.current.user).toBeNull();
    expect(localStorage.getItem('specodex.auth.tokens')).toBeNull();
    expect(apiClient.setAuthToken).toHaveBeenLastCalledWith(null);
    // Phase 5c: refresh token revocation. logout() fires
    // authLogout with the snapshotted refresh token before
    // tokens go to null. Fire-and-forget; we don't await the
    // promise but the call itself must happen.
    expect(apiClient.authLogout).toHaveBeenCalledWith('r');
  });

  it('does not call authLogout when there is no refresh token', () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    // No prior login — calling logout shouldn't try to revoke a
    // token that doesn't exist.
    act(() => { result.current.logout(); });
    expect(apiClient.authLogout).not.toHaveBeenCalled();
  });
});

describe('auto-refresh', () => {
  it('refreshes shortly before token expiry', async () => {
    const exp = Math.floor(Date.now() / 1000) + 120; // 2 min from now
    const idToken = fakeIdToken({ sub: 'u', email: 'e', exp });
    const newExp = Math.floor(Date.now() / 1000) + 3600;
    const newIdToken = fakeIdToken({ sub: 'u', email: 'e', exp: newExp });

    (apiClient.authRefresh as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      id_token: newIdToken,
      access_token: 'a2',
      expires_in: 3600,
    });

    localStorage.setItem('specodex.auth.tokens', JSON.stringify({
      idToken, accessToken: 'a', refreshToken: 'r',
    }));

    renderHook(() => useAuth(), { wrapper });

    // Auto-refresh should fire ~60s before exp = ~60s from now
    await act(async () => {
      await vi.advanceTimersByTimeAsync(70_000);
    });

    expect(apiClient.authRefresh).toHaveBeenCalledWith('r');
  });

  it('logs out on refresh failure', async () => {
    const exp = Math.floor(Date.now() / 1000) + 120;
    const idToken = fakeIdToken({ sub: 'u', email: 'e', exp });

    (apiClient.authRefresh as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error('expired refresh'));

    localStorage.setItem('specodex.auth.tokens', JSON.stringify({
      idToken, accessToken: 'a', refreshToken: 'r',
    }));

    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.user).not.toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(70_000);
    });

    await waitFor(() => expect(result.current.user).toBeNull());
    expect(localStorage.getItem('specodex.auth.tokens')).toBeNull();
  });
});

describe('register flow', () => {
  it('returns the next-step hint without setting user', async () => {
    (apiClient.authRegister as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      next: 'confirm',
      message: 'Check email',
    });

    const { result } = renderHook(() => useAuth(), { wrapper });
    let outcome: { next: string } | null = null;
    await act(async () => {
      outcome = await result.current.register('new@example.com', 'CorrectHorse9Battery');
    });

    expect(outcome).toEqual({ next: 'confirm', message: 'Check email' });
    expect(result.current.user).toBeNull();
  });
});

describe('AuthProvider rendering', () => {
  it('renders children and exposes context', () => {
    const Probe = () => {
      const { user } = useAuth();
      return <div data-testid="probe">{user ? 'in' : 'out'}</div>;
    };
    const { getByTestId } = render(<AuthProvider><Probe /></AuthProvider>);
    expect(getByTestId('probe').textContent).toBe('out');
  });
});
