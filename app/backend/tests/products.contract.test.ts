/**
 * Contract tests for /api/products.
 *
 * These routes are mutation-gated in public mode by the readonly middleware,
 * so these tests bypass that gate (Jest env → APP_MODE is not 'public' or
 * the in-process tests hit /api/products directly via express). The intent is
 * to catch boundary conditions in the route handlers themselves.
 */

import request from 'supertest';
import app from '../src/index';
import { DynamoDBService } from '../src/db/dynamodb';

jest.mock('../src/db/dynamodb');

beforeEach(() => {
  jest.clearAllMocks();
});

/** Build a base64url cursor the way the route does. */
const cursorOf = (next: { type: string; key?: Record<string, unknown> }) =>
  Buffer.from(JSON.stringify(next)).toString('base64url');

describe('GET /api/products — listing surface', () => {
  it('returns 200 with empty array when no products', async () => {
    (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({ items: [] });
    const res = await request(app).get('/api/products');
    expect(res.status).toBe(200);
    expect(res.body.data).toEqual([]);
    expect(res.body.count).toBe(0);
    expect(res.body.cursor).toBeNull();
  });

  it('500 on DB failure — JSON error body', async () => {
    (DynamoDBService.prototype.listPage as jest.Mock).mockRejectedValue(new Error('boom'));
    const res = await request(app).get('/api/products');
    expect(res.status).toBe(500);
    expect(res.body).toHaveProperty('success', false);
    expect(res.body.error).not.toMatch(/at \S+\.ts/);
  });

  it('limit=NaN falls back to the default cap — does not crash', async () => {
    (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({ items: [] });
    const res = await request(app).get('/api/products?limit=abc');
    expect(res.status).toBeLessThan(500);
    // Default kicks in when limit can't be parsed.
    expect((DynamoDBService.prototype.listPage as jest.Mock).mock.calls[0][1]).toBe(2000);
  });

  it('unknown type still returns 200 (route does not validate type)', async () => {
    // Documents current policy: type validation happens at search, not list.
    (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({ items: [] });
    const res = await request(app).get('/api/products?type=not-a-real-type');
    expect(res.status).toBe(200);
  });

  it('applies the default 2000-row cap when no limit param is given', async () => {
    (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({ items: [] });
    await request(app).get('/api/products?type=drive');
    expect((DynamoDBService.prototype.listPage as jest.Mock).mock.calls[0][1]).toBe(2000);
  });

  it('respects an explicit numeric limit', async () => {
    (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({ items: [] });
    await request(app).get('/api/products?type=motor&limit=50');
    expect((DynamoDBService.prototype.listPage as jest.Mock).mock.calls[0][1]).toBe(50);
  });

  it('sets truncated=true and returns a cursor when more pages exist', async () => {
    (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({
      items: [{ product_id: 'p1', product_type: 'motor', manufacturer: 'X' }],
      next: { type: 'motor', key: { PK: { S: 'PRODUCT#MOTOR' }, SK: { S: 'PRODUCT#p1' } } },
    });
    const res = await request(app).get('/api/products?type=motor');
    expect(res.status).toBe(200);
    expect(res.body.truncated).toBe(true);
    expect(typeof res.body.cursor).toBe('string');
    // The cursor round-trips through the route's own encoding.
    const decoded = JSON.parse(Buffer.from(res.body.cursor, 'base64url').toString('utf8'));
    expect(decoded.type).toBe('motor');
    expect(decoded.key.SK).toEqual({ S: 'PRODUCT#p1' });
  });

  it('sets truncated=false and cursor=null when the listing is exhausted', async () => {
    (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({
      items: [{ product_id: 'p1', product_type: 'motor', manufacturer: 'X' }],
    });
    const res = await request(app).get('/api/products?type=motor');
    expect(res.body.truncated).toBe(false);
    expect(res.body.cursor).toBeNull();
  });

  it('passes a valid cursor through to db.listPage as the start point', async () => {
    (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({ items: [] });
    const cursor = cursorOf({
      type: 'motor',
      key: { PK: { S: 'PRODUCT#MOTOR' }, SK: { S: 'PRODUCT#p42' } },
    });
    const res = await request(app).get(`/api/products?type=motor&cursor=${cursor}`);
    expect(res.status).toBe(200);
    const start = (DynamoDBService.prototype.listPage as jest.Mock).mock.calls[0][2];
    expect(start.type).toBe('motor');
    expect(start.key.SK).toEqual({ S: 'PRODUCT#p42' });
  });

  // Cursors are attacker-controlled input. Every malformed shape must
  // 400 (never 500, never reach DynamoDB).
  describe('cursor hardening', () => {
    const badCursors: Array<[string, string]> = [
      ['garbage base64', '!!!not-base64!!!'],
      ['valid base64 of non-JSON', Buffer.from('not json').toString('base64url')],
      ['JSON array instead of object', Buffer.from('[1,2]').toString('base64url')],
      ['missing type', cursorOf({ type: undefined as unknown as string })],
      ['unknown type', cursorOf({ type: 'warp_drive' })],
      ['key with non-scalar attribute', cursorOf({ type: 'motor', key: { PK: { M: {} } } })],
      ['key with multi-entry attribute', cursorOf({ type: 'motor', key: { PK: { S: 'a', N: '1' } } })],
      ['key with non-string attribute value', cursorOf({ type: 'motor', key: { PK: { S: 7 } } })],
      ['key as array', cursorOf({ type: 'motor', key: [1] as unknown as Record<string, unknown> })],
      ['oversized cursor', 'A'.repeat(5000)],
    ];

    it.each(badCursors)('%s → 400', async (_name, cursor) => {
      (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({ items: [] });
      const res = await request(app).get(
        `/api/products?type=motor&cursor=${encodeURIComponent(cursor)}`
      );
      expect(res.status).toBe(400);
      expect(res.body.success).toBe(false);
      expect(DynamoDBService.prototype.listPage).not.toHaveBeenCalled();
    });

    it('cursor minted for another type filter → 400', async () => {
      (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({ items: [] });
      const cursor = cursorOf({ type: 'drive' });
      const res = await request(app).get(`/api/products?type=motor&cursor=${cursor}`);
      expect(res.status).toBe(400);
      expect(DynamoDBService.prototype.listPage).not.toHaveBeenCalled();
    });

    it('type-scoped cursor is accepted on a type=all listing', async () => {
      (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({ items: [] });
      const cursor = cursorOf({ type: 'drive' });
      const res = await request(app).get(`/api/products?cursor=${cursor}`);
      expect(res.status).toBe(200);
      expect((DynamoDBService.prototype.listPage as jest.Mock).mock.calls[0][2].type).toBe('drive');
    });
  });
});

describe('GET /api/products/:id — single-read surface', () => {
  it('returns 400 when type query param is missing', async () => {
    const res = await request(app).get('/api/products/some-id');
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/type/);
  });

  it('returns 404 when db.read returns null', async () => {
    (DynamoDBService.prototype.read as jest.Mock).mockResolvedValue(null);
    const res = await request(app).get('/api/products/nonexistent?type=motor');
    expect(res.status).toBe(404);
  });

  it('URL-encoded slashes in id do not crash', async () => {
    (DynamoDBService.prototype.read as jest.Mock).mockResolvedValue(null);
    const res = await request(app).get('/api/products/abc%2Fdef?type=motor');
    expect(res.status).toBeLessThan(500);
  });
});

describe('POST /api/products — creation surface', () => {
  it('returns 400 when product_type missing on single body', async () => {
    const res = await request(app).post('/api/products').send({ manufacturer: 'X' });
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/product_type/);
  });

  it('returns 400 when manufacturer missing on single body', async () => {
    const res = await request(app).post('/api/products').send({ product_type: 'motor' });
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/manufacturer/);
  });

  it('single-item array is valid', async () => {
    (DynamoDBService.prototype.create as jest.Mock).mockResolvedValue(true);
    const res = await request(app).post('/api/products').send([
      { product_type: 'motor', manufacturer: 'X' },
    ]);
    expect(res.status).toBe(201);
    expect(res.body.data.items_received).toBe(1);
  });

  it('batch with one invalid entry rejects the whole batch', async () => {
    const res = await request(app).post('/api/products').send([
      { product_type: 'motor', manufacturer: 'X' },
      { product_type: 'motor' }, // missing manufacturer
    ]);
    expect(res.status).toBe(400);
  });

  it('empty body is 400', async () => {
    const res = await request(app).post('/api/products').send({});
    expect(res.status).toBe(400);
  });

  it('primitive JSON body (string) returns 400', async () => {
    const res = await request(app)
      .post('/api/products')
      .set('Content-Type', 'application/json')
      .send('"a string"');
    expect(res.status).toBe(400);
    expect(res.body.success).toBe(false);
  });

  it('null JSON body returns 400', async () => {
    const res = await request(app)
      .post('/api/products')
      .set('Content-Type', 'application/json')
      .send('null');
    expect(res.status).toBe(400);
  });

  it('array containing a primitive returns 400', async () => {
    const res = await request(app).post('/api/products').send([
      { product_type: 'motor', manufacturer: 'X' },
      'not-an-object',
    ]);
    expect(res.status).toBe(400);
  });

  it('huge batch does not crash (1000 items)', async () => {
    (DynamoDBService.prototype.batchCreate as jest.Mock).mockResolvedValue(1000);
    const items = Array.from({ length: 1000 }, () => ({
      product_type: 'motor',
      manufacturer: 'X',
    }));
    const res = await request(app).post('/api/products').send(items);
    expect(res.status).toBeLessThan(500);
  });
});

describe('DELETE /api/products/:id — idempotence', () => {
  it('DELETE non-existent id does not 500', async () => {
    (DynamoDBService.prototype.delete as jest.Mock).mockResolvedValue(false);
    const res = await request(app).delete('/api/products/nonexistent?type=motor');
    expect(res.status).toBeLessThan(500);
  });
});
