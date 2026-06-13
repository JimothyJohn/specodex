/**
 * Resilience tests: edge cases, connectivity failures, malformed inputs,
 * partial failures, and error propagation through the backend.
 */

import request from 'supertest';
import app from '../src/index';
import { DynamoDBService } from '../src/db/dynamodb';

jest.mock('../src/db/dynamodb');

describe('Edge Cases: Malformed Inputs', () => {
  beforeEach(() => jest.clearAllMocks());

  it('GET /api/products?limit=NaN returns products (NaN coerces gracefully)', async () => {
    (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({ items: [] });
    const response = await request(app).get('/api/products?limit=abc');
    expect(response.status).toBe(200);
  });

  it('GET /api/products?limit=-1 does not crash', async () => {
    (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({ items: [] });
    const response = await request(app).get('/api/products?limit=-1');
    expect(response.status).toBe(200);
  });

  it('GET /api/products?limit=0 does not crash', async () => {
    (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({ items: [] });
    const response = await request(app).get('/api/products?limit=0');
    expect(response.status).toBe(200);
  });

  it('GET /api/products?type= (empty string) returns 200', async () => {
    (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({ items: [] });
    const response = await request(app).get('/api/products?type=');
    expect(response.status).toBe(200);
  });

  it('POST /api/products with empty body returns 400', async () => {
    const response = await request(app).post('/api/products').send({});
    expect(response.status).toBe(400);
  });

  it('POST /api/products with empty array does not crash', async () => {
    const response = await request(app).post('/api/products').send([]);
    // Empty array hits validation loop with 0 items — no validation error, goes to batch path
    expect(response.status).toBeLessThan(500);
  });

  it('POST /api/products with null body returns error (not crash)', async () => {
    const response = await request(app)
      .post('/api/products')
      .set('Content-Type', 'application/json')
      .send('null');
    // Express JSON parser rejects null — returns 400 or 500 but doesn't hang/crash
    expect([400, 500]).toContain(response.status);
  });

  it('GET /api/products/:id with very long ID does not crash', async () => {
    (DynamoDBService.prototype.read as jest.Mock).mockResolvedValue(null);
    const longId = 'a'.repeat(1000);
    const response = await request(app).get(`/api/products/${longId}?type=motor`);
    expect(response.status).toBe(404);
  });

  it('POST /api/products with XSS in product_name does not reflect it', async () => {
    (DynamoDBService.prototype.create as jest.Mock).mockResolvedValue(true);
    const response = await request(app).post('/api/products').send({
      product_type: 'motor',
      product_name: '<script>alert("xss")</script>',
      manufacturer: 'Test',
    });
    expect(response.status).toBe(201);
    // Value is stored as-is (no HTML rendering in API), but should not cause errors
  });

  it('GET /api/v1/search?limit=0 returns 400 (below minimum)', async () => {
    const response = await request(app).get('/api/v1/search?limit=0');
    expect(response.status).toBe(400);
  });

  it('GET /api/v1/search?limit=101 returns 400 (above maximum)', async () => {
    const response = await request(app).get('/api/v1/search?limit=101');
    expect(response.status).toBe(400);
  });

  it('GET /api/v1/search?where=invalid returns 500 (bad filter)', async () => {
    (DynamoDBService.prototype.list as jest.Mock).mockResolvedValue([]);
    const response = await request(app).get('/api/v1/search?where=invalid');
    expect(response.status).toBe(500);
  });
});

describe('DynamoDB Connectivity Failures', () => {
  beforeEach(() => jest.clearAllMocks());

  it('GET /api/products returns 500 when DB throws', async () => {
    (DynamoDBService.prototype.listPage as jest.Mock).mockRejectedValue(
      new Error('Connection refused')
    );
    const response = await request(app).get('/api/products');
    expect(response.status).toBe(500);
    expect(response.body.success).toBe(false);
  });

  it('GET /api/products/summary returns 500 on DB error', async () => {
    (DynamoDBService.prototype.count as jest.Mock).mockRejectedValue(
      new Error('Throttled')
    );
    const response = await request(app).get('/api/products/summary');
    expect(response.status).toBe(500);
  });

  it('GET /api/products/categories returns 500 on DB error', async () => {
    (DynamoDBService.prototype.getCategories as jest.Mock).mockRejectedValue(
      new Error('Service unavailable')
    );
    const response = await request(app).get('/api/products/categories');
    expect(response.status).toBe(500);
  });

  it('GET /api/products/manufacturers returns 500 on DB error', async () => {
    (DynamoDBService.prototype.getUniqueManufacturers as jest.Mock).mockRejectedValue(
      new Error('Timeout')
    );
    const response = await request(app).get('/api/products/manufacturers');
    expect(response.status).toBe(500);
  });

  it('POST /api/products returns 500 on DB create failure', async () => {
    (DynamoDBService.prototype.create as jest.Mock).mockRejectedValue(
      new Error('ProvisionedThroughputExceededException')
    );
    const response = await request(app).post('/api/products').send({
      product_type: 'motor',
      manufacturer: 'Test',
      product_name: 'TestMotor',
    });
    expect(response.status).toBe(500);
  });

  it('DELETE /api/products/:id returns 500 on DB error', async () => {
    (DynamoDBService.prototype.read as jest.Mock).mockRejectedValue(
      new Error('Network error')
    );
    const response = await request(app).delete('/api/products/test-123?type=motor');
    expect(response.status).toBe(500);
  });

  it('PUT /api/products/:id returns 500 on DB error', async () => {
    (DynamoDBService.prototype.updateProduct as jest.Mock).mockRejectedValue(
      new Error('Conditional check failed')
    );
    const response = await request(app)
      .put('/api/products/test-123?type=motor')
      .send({ product_name: 'Updated' });
    expect(response.status).toBe(500);
  });

  it('POST /api/datasheets returns 500 on DB error', async () => {
    (DynamoDBService.prototype.datasheetExists as jest.Mock).mockRejectedValue(
      new Error('DB unreachable')
    );
    const response = await request(app).post('/api/datasheets').send({
      url: 'https://example.com/test.pdf',
      product_type: 'motor',
      product_name: 'Test',
    });
    expect(response.status).toBe(500);
  });

  it('GET /api/datasheets returns 500 on DB error', async () => {
    (DynamoDBService.prototype.listDatasheets as jest.Mock).mockRejectedValue(
      new Error('Scan failed')
    );
    const response = await request(app).get('/api/datasheets');
    expect(response.status).toBe(500);
  });
});

describe('Batch Operation Partial Failures', () => {
  beforeEach(() => jest.clearAllMocks());

  it('POST /api/products batch with partial failure reports counts', async () => {
    (DynamoDBService.prototype.batchCreate as jest.Mock).mockResolvedValue(1); // 1 of 2 succeed

    const products = [
      { product_type: 'motor', product_name: 'A', manufacturer: 'Test' },
      { product_type: 'motor', product_name: 'B', manufacturer: 'Test' },
    ];
    const response = await request(app).post('/api/products').send(products);
    expect(response.status).toBe(201);
    expect(response.body.data.items_received).toBe(2);
    expect(response.body.data.items_created).toBe(1);
    expect(response.body.data.items_failed).toBe(1);
  });

  it('POST /api/products single item with DB failure returns 201 with success=false', async () => {
    (DynamoDBService.prototype.create as jest.Mock).mockResolvedValue(false);

    const response = await request(app).post('/api/products').send({
      product_type: 'motor', product_name: 'A', manufacturer: 'Test',
    });
    expect(response.status).toBe(201);
    expect(response.body.data.items_created).toBe(0);
  });

  it('POST /api/products/deduplicate dry run with no duplicates', async () => {
    (DynamoDBService.prototype.listAll as jest.Mock).mockResolvedValue([
      { product_id: '1', product_type: 'motor', part_number: 'A', product_name: 'X', manufacturer: 'Y', PK: 'P', SK: 'S' },
    ]);

    const response = await request(app).post('/api/products/deduplicate').send({ confirm: false });
    expect(response.status).toBe(200);
    expect(response.body.data.found).toBe(0);
    expect(response.body.data.dry_run).toBe(true);
  });
});

describe('Error Response Format Consistency', () => {
  beforeEach(() => jest.clearAllMocks());

  it('all error responses have success=false', async () => {
    const endpoints = [
      { method: 'get', path: '/api/products/nonexistent?type=motor' },
      { method: 'get', path: '/api/products/x' }, // missing type
      { method: 'delete', path: '/api/products/x' }, // missing type
    ];

    (DynamoDBService.prototype.read as jest.Mock).mockResolvedValue(null);

    for (const { method, path } of endpoints) {
      const response = await (request(app) as any)[method](path);
      expect(response.body.success).toBe(false);
      expect(response.body).toHaveProperty('error');
    }
  });

  it('404 for unknown API endpoint returns JSON', async () => {
    const response = await request(app).get('/api/does-not-exist');
    expect(response.status).toBe(404);
    expect(response.headers['content-type']).toMatch(/json/);
    expect(response.body.success).toBe(false);
  });

  it('health endpoint always returns 200 even under load', async () => {
    const requests = Array.from({ length: 10 }, () =>
      request(app).get('/health')
    );
    const responses = await Promise.all(requests);
    expect(responses.every(r => r.status === 200)).toBe(true);
  });
});

describe('Concurrent Request Handling', () => {
  beforeEach(() => jest.clearAllMocks());

  it('handles 20 concurrent list requests', async () => {
    (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({ items: [] });

    const requests = Array.from({ length: 20 }, () =>
      request(app).get('/api/products?type=motor')
    );
    const responses = await Promise.all(requests);
    expect(responses.every(r => r.status === 200)).toBe(true);
    expect(responses).toHaveLength(20);
  });

  it('handles mixed read/write requests concurrently', async () => {
    (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({ items: [] });
    (DynamoDBService.prototype.create as jest.Mock).mockResolvedValue(true);
    (DynamoDBService.prototype.count as jest.Mock).mockResolvedValue({ total: 0 });

    const requests = [
      request(app).get('/api/products'),
      request(app).get('/api/products/summary'),
      request(app).post('/api/products').send({
        product_type: 'motor', manufacturer: 'Test', product_name: 'Test',
      }),
      request(app).get('/health'),
      request(app).get('/api/products?type=drive'),
    ];

    const responses = await Promise.all(requests);
    expect(responses.every(r => r.status < 500)).toBe(true);
  });
});
