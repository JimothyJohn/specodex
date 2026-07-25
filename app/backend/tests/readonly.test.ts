/**
 * Readonly guard tests.
 *
 * The public deployment blocks all writes except specific allowed paths.
 * These tests ensure the guard works correctly so that agents (who only
 * use the admin API) and the public site don't interfere with each other.
 */

import { readonlyGuard } from '../src/middleware/readonly';
import { Request, Response, NextFunction } from 'express';

function mockReq(method: string, path: string): Partial<Request> {
  return { method, path };
}

function mockRes(): Partial<Response> & { _status: number; _body: any } {
  const res: any = {
    _status: 0,
    _body: null,
    status(code: number) {
      res._status = code;
      return res;
    },
    json(body: any) {
      res._body = body;
      return res;
    },
  };
  return res;
}

describe('readonlyGuard', () => {
  let nextCalled: boolean;
  const next: NextFunction = () => { nextCalled = true; };

  beforeEach(() => {
    nextCalled = false;
  });

  // =================== Read Methods Pass Through ===================

  describe('allows read methods', () => {
    it.each(['GET', 'HEAD', 'OPTIONS'])('%s passes through', (method) => {
      const req = mockReq(method, '/products');
      const res = mockRes();
      readonlyGuard(req as Request, res as Response, next);
      expect(nextCalled).toBe(true);
      expect(res._status).toBe(0);
    });
  });

  // =================== Write Methods Blocked ===================

  describe('blocks write methods on protected paths', () => {
    it.each(['POST', 'PUT', 'DELETE', 'PATCH'])('%s /products returns 403', (method) => {
      const req = mockReq(method, '/products');
      const res = mockRes();
      readonlyGuard(req as Request, res as Response, next);
      expect(nextCalled).toBe(false);
      expect(res._status).toBe(403);
      expect(res._body.success).toBe(false);
      expect(res._body.error).toContain('read-only');
    });

    it('blocks POST /datasheets', () => {
      const req = mockReq('POST', '/datasheets');
      const res = mockRes();
      readonlyGuard(req as Request, res as Response, next);
      expect(nextCalled).toBe(false);
      expect(res._status).toBe(403);
    });

    it('blocks DELETE /products/123', () => {
      const req = mockReq('DELETE', '/products/123');
      const res = mockRes();
      readonlyGuard(req as Request, res as Response, next);
      expect(nextCalled).toBe(false);
      expect(res._status).toBe(403);
    });

    it('blocks POST /products/deduplicate', () => {
      const req = mockReq('POST', '/products/deduplicate');
      const res = mockRes();
      readonlyGuard(req as Request, res as Response, next);
      expect(nextCalled).toBe(false);
      expect(res._status).toBe(403);
    });
  });

  // =================== Allowed Write Paths ===================

  describe('allows writes to exempt paths', () => {
    it('POST /upload passes through', () => {
      const req = mockReq('POST', '/upload');
      const res = mockRes();
      readonlyGuard(req as Request, res as Response, next);
      expect(nextCalled).toBe(true);
    });

    it('POST /upload/ passes through', () => {
      const req = mockReq('POST', '/upload/');
      const res = mockRes();
      readonlyGuard(req as Request, res as Response, next);
      expect(nextCalled).toBe(true);
    });

    // The compat check is a read-only computation that uses POST only to
    // carry a body ({a, b} product refs) — it must survive public mode.
    // The guard is mounted at app.use('/api', ...) in src/index.ts, so the
    // path it sees for POST /api/v1/compat/check is /v1/compat/check.
    it('POST /v1/compat/check passes through (read-only computation)', () => {
      const req = mockReq('POST', '/v1/compat/check');
      const res = mockRes();
      readonlyGuard(req as Request, res as Response, next);
      expect(nextCalled).toBe(true);
      expect(res._status).toBe(0);
    });

    it('POST /v1/compat/check/ passes through (trailing slash)', () => {
      const req = mockReq('POST', '/v1/compat/check/');
      const res = mockRes();
      readonlyGuard(req as Request, res as Response, next);
      expect(nextCalled).toBe(true);
    });

    it('compat exemption is exact-match — sibling compat paths still 403', () => {
      for (const path of ['/v1/compat', '/v1/compat/adjacent', '/v1/compat/checkother', '/v1/compat/check/extra']) {
        const req = mockReq('POST', path);
        const res = mockRes();
        nextCalled = false;
        readonlyGuard(req as Request, res as Response, next);
        expect(nextCalled).toBe(false);
        expect(res._status).toBe(403);
      }
    });
  });

  // =================== Edge Cases ===================

  describe('edge cases', () => {
    it('does not allow write to path that starts with /upload but is different', () => {
      const req = mockReq('POST', '/upload-something');
      const res = mockRes();
      readonlyGuard(req as Request, res as Response, next);
      expect(nextCalled).toBe(false);
      expect(res._status).toBe(403);
    });

    it('GET on exempt paths still passes (redundant but safe)', () => {
      const req = mockReq('GET', '/upload');
      const res = mockRes();
      readonlyGuard(req as Request, res as Response, next);
      expect(nextCalled).toBe(true);
    });
  });
});
