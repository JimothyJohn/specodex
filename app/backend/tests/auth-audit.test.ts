/**
 * Tests for the auth audit emitter and routes integration.
 *
 * Two layers:
 *   1. Unit: emitAuthEvent + auditMeta (format, IP precedence,
 *      UA truncation, never-throws guarantee).
 *   2. Integration: hit /api/auth/login + /api/auth/register and
 *      assert AUTH_EVENT lines hit stdout with expected fields,
 *      including never-log-the-password / never-log-the-token
 *      guarantees.
 */

import { Request } from 'express';
import { emitAuthEvent, auditMeta } from '../src/middleware/auth-audit';

describe('emitAuthEvent', () => {
  let logSpy: jest.SpyInstance;

  beforeEach(() => {
    logSpy = jest.spyOn(console, 'log').mockImplementation(() => undefined);
  });

  afterEach(() => {
    logSpy.mockRestore();
  });

  it('emits a single line with the AUTH_EVENT prefix and JSON body', () => {
    emitAuthEvent({ event: 'login', success: true, email: 'a@b.test' });
    expect(logSpy).toHaveBeenCalledTimes(1);
    const line = logSpy.mock.calls[0][0] as string;
    expect(line.startsWith('AUTH_EVENT ')).toBe(true);
    const body = JSON.parse(line.slice('AUTH_EVENT '.length));
    expect(body).toEqual({ event: 'login', success: true, email: 'a@b.test' });
  });

  it('does not throw when JSON.stringify fails (circular reference)', () => {
    const circular: { event: string; success: boolean; self?: unknown } = {
      event: 'login',
      success: false,
    };
    circular.self = circular;
    expect(() => emitAuthEvent(circular as never)).not.toThrow();
  });

  it('round-trips all AuthAuditEvent fields', () => {
    emitAuthEvent({
      event: 'register',
      success: false,
      email: 'a@b.test',
      sub: 'cognito-uuid',
      ip: '1.2.3.4',
      userAgent: 'Mozilla/5.0 ...',
      errorCode: 'UsernameExistsException',
      durationMs: 142,
    });
    const line = logSpy.mock.calls[0][0] as string;
    const body = JSON.parse(line.slice('AUTH_EVENT '.length));
    expect(body.event).toBe('register');
    expect(body.errorCode).toBe('UsernameExistsException');
    expect(body.durationMs).toBe(142);
  });
});

describe('auditMeta', () => {
  function reqWith(headers: Record<string, string | string[]>, ip = '127.0.0.1'): Request {
    return { headers, ip } as unknown as Request;
  }

  it('returns the first hop of X-Forwarded-For when present', () => {
    const meta = auditMeta(reqWith({ 'x-forwarded-for': '203.0.113.7, 10.0.0.1' }));
    expect(meta.ip).toBe('203.0.113.7');
  });

  it('handles X-Forwarded-For as an array', () => {
    const meta = auditMeta(reqWith({ 'x-forwarded-for': ['203.0.113.7', '10.0.0.1'] }));
    expect(meta.ip).toBe('203.0.113.7');
  });

  it('falls back to req.ip when no X-Forwarded-For header', () => {
    const meta = auditMeta(reqWith({}, '203.0.113.99'));
    expect(meta.ip).toBe('203.0.113.99');
  });

  it('truncates user-agent at 256 chars', () => {
    const longUa = 'X'.repeat(500);
    const meta = auditMeta(reqWith({ 'user-agent': longUa }));
    expect(meta.userAgent?.length).toBe(256);
  });

  it('passes shorter user-agent unchanged', () => {
    const ua = 'Mozilla/5.0 Test';
    const meta = auditMeta(reqWith({ 'user-agent': ua }));
    expect(meta.userAgent).toBe(ua);
  });
});

// =================== Routes integration ===================

const mockSend = jest.fn();

jest.mock('@aws-sdk/client-cognito-identity-provider', () => {
  class FakeCommand {
    constructor(public input: unknown) {}
  }
  return {
    CognitoIdentityProviderClient: jest.fn().mockImplementation(() => ({ send: mockSend })),
    SignUpCommand: class extends FakeCommand { _kind = 'SignUp'; },
    ConfirmSignUpCommand: class extends FakeCommand { _kind = 'ConfirmSignUp'; },
    InitiateAuthCommand: class extends FakeCommand { _kind = 'InitiateAuth'; },
    ForgotPasswordCommand: class extends FakeCommand { _kind = 'ForgotPassword'; },
    ConfirmForgotPasswordCommand: class extends FakeCommand { _kind = 'ConfirmForgotPassword'; },
    ResendConfirmationCodeCommand: class extends FakeCommand { _kind = 'ResendConfirmationCode'; },
  };
});

jest.mock('../src/db/dynamodb');

import request from 'supertest';
import config from '../src/config';
import app from '../src/index';

const VALID_PASSWORD = 'CorrectHorse9Battery';

describe('routes/auth.ts emits audit events', () => {
  let logSpy: jest.SpyInstance;

  beforeEach(() => {
    mockSend.mockReset();
    logSpy = jest.spyOn(console, 'log').mockImplementation(() => undefined);
    config.cognito.userPoolClientId = 'test-client-id';
    config.cognito.userPoolId = 'us-east-1_TEST';
  });

  afterEach(() => {
    logSpy.mockRestore();
  });

  function findAuthEvents(): Array<Record<string, unknown>> {
    return logSpy.mock.calls
      .map(c => c[0])
      .filter(line => typeof line === 'string' && line.startsWith('AUTH_EVENT '))
      .map(line => JSON.parse((line as string).slice('AUTH_EVENT '.length)));
  }

  it('login success emits a success=true event with the email', async () => {
    mockSend.mockResolvedValueOnce({
      AuthenticationResult: {
        IdToken: 'id', AccessToken: 'a', RefreshToken: 'r', ExpiresIn: 3600,
      },
    });

    await request(app)
      .post('/api/auth/login')
      .set('User-Agent', 'jest-test')
      .send({ email: 'user@example.com', password: VALID_PASSWORD });

    const events = findAuthEvents();
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      event: 'login',
      success: true,
      email: 'user@example.com',
      userAgent: 'jest-test',
    });
    expect(typeof events[0].durationMs).toBe('number');
  });

  it('login failure emits success=false with the Cognito error code', async () => {
    const err = new Error('invalid creds');
    err.name = 'NotAuthorizedException';
    mockSend.mockRejectedValueOnce(err);

    await request(app)
      .post('/api/auth/login')
      .send({ email: 'user@example.com', password: VALID_PASSWORD });

    const events = findAuthEvents();
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      event: 'login',
      success: false,
      email: 'user@example.com',
      errorCode: 'NotAuthorizedException',
    });
  });

  it('NEVER logs the password or any token in any audit event', async () => {
    // Both success and failure paths
    mockSend.mockResolvedValueOnce({
      AuthenticationResult: {
        IdToken: 'eyJ-id-token', AccessToken: 'eyJ-access-token',
        RefreshToken: 'r-secret', ExpiresIn: 3600,
      },
    });
    await request(app)
      .post('/api/auth/login')
      .send({ email: 'user@example.com', password: 'TopSecret9Password' });

    const err = new Error('bad');
    err.name = 'NotAuthorizedException';
    mockSend.mockRejectedValueOnce(err);
    await request(app)
      .post('/api/auth/login')
      .send({ email: 'user2@example.com', password: 'AnotherSecretXXX' });

    const allLogText = logSpy.mock.calls.map(c => String(c[0])).join('\n');
    expect(allLogText).not.toContain('TopSecret9Password');
    expect(allLogText).not.toContain('AnotherSecretXXX');
    expect(allLogText).not.toContain('eyJ-id-token');
    expect(allLogText).not.toContain('eyJ-access-token');
    expect(allLogText).not.toContain('r-secret');
  });

  it('register success emits a register event', async () => {
    mockSend.mockResolvedValueOnce({});
    await request(app)
      .post('/api/auth/register')
      .send({ email: 'new@example.com', password: VALID_PASSWORD });

    const events = findAuthEvents();
    expect(events.some(e => e.event === 'register' && e.success === true)).toBe(true);
  });

  it('register failure (UsernameExistsException) emits success=false', async () => {
    const err = new Error('exists');
    err.name = 'UsernameExistsException';
    mockSend.mockRejectedValueOnce(err);

    await request(app)
      .post('/api/auth/register')
      .send({ email: 'taken@example.com', password: VALID_PASSWORD });

    const events = findAuthEvents();
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      event: 'register',
      success: false,
      errorCode: 'UsernameExistsException',
    });
  });
});
