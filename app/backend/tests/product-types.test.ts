/**
 * Product type consistency tests.
 *
 * Guards against the class of bug where a product type is supported
 * in one layer (e.g. DB, models) but missing from another (e.g. routes,
 * search validation). When multiple agents collaborate on the codebase,
 * these seams are where things break.
 */

import request from 'supertest';
import app from '../src/index';
import { DynamoDBService } from '../src/db/dynamodb';
import { VALID_PRODUCT_TYPES, formatDisplayName } from '../src/config/productTypes';

jest.mock('../src/db/dynamodb');

// All configured types are hardware product types. 'datasheet' (document metadata)
// is intentionally NOT in VALID_PRODUCT_TYPES — it has its own /api/datasheets
// route and must not appear in the public product-type dropdown.
const HARDWARE_TYPES = VALID_PRODUCT_TYPES;

// =================== Config Consistency ===================

describe('Product Type Configuration', () => {
  it('VALID_PRODUCT_TYPES contains all expected hardware types', () => {
    expect(VALID_PRODUCT_TYPES).toContain('motor');
    expect(VALID_PRODUCT_TYPES).toContain('drive');
    expect(VALID_PRODUCT_TYPES).toContain('gearhead');
    expect(VALID_PRODUCT_TYPES).toContain('robot_arm');
    expect(VALID_PRODUCT_TYPES).toContain('contactor');
  });

  it('VALID_PRODUCT_TYPES does not include datasheet (metadata, not a product)', () => {
    expect(VALID_PRODUCT_TYPES).not.toContain('datasheet' as any);
  });

  it('formatDisplayName handles all types', () => {
    for (const type of VALID_PRODUCT_TYPES) {
      const name = formatDisplayName(type);
      expect(name.length).toBeGreaterThan(0);
      // Should end with 's' (pluralized)
      expect(name.endsWith('s')).toBe(true);
    }
  });

  it('formatDisplayName handles underscored types', () => {
    expect(formatDisplayName('robot_arm')).toBe('Robot Arms');
  });
});

// =================== Routes Per Product Type ===================

describe('API routes work for all product types', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  describe('GET /api/products?type=<type>', () => {
    it.each(HARDWARE_TYPES)('lists %s products', async (type) => {
      const mockProducts = [
        {
          product_id: `${type}-001`,
          product_type: type,
          manufacturer: 'TestCorp',
          PK: `PRODUCT#${type.toUpperCase()}`,
          SK: `PRODUCT#${type}-001`,
        },
      ];
      (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({ items: mockProducts });

      const response = await request(app).get(`/api/products?type=${type}`);
      expect(response.status).toBe(200);
      expect(response.body.success).toBe(true);
      expect(response.body.data).toHaveLength(1);
      expect(response.body.data[0].product_type).toBe(type);
    });
  });

  describe('GET /api/products/:id?type=<type>', () => {
    it.each(HARDWARE_TYPES)('fetches a single %s product', async (type) => {
      const mockProduct = {
        product_id: `${type}-001`,
        product_type: type,
        manufacturer: 'TestCorp',
        PK: `PRODUCT#${type.toUpperCase()}`,
        SK: `PRODUCT#${type}-001`,
      };
      (DynamoDBService.prototype.read as jest.Mock).mockResolvedValue(mockProduct);

      const response = await request(app).get(`/api/products/${type}-001?type=${type}`);
      expect(response.status).toBe(200);
      expect(response.body.success).toBe(true);
      expect(response.body.data.product_type).toBe(type);
    });
  });

  describe('POST /api/products', () => {
    it.each(HARDWARE_TYPES)('creates a %s product', async (type) => {
      (DynamoDBService.prototype.create as jest.Mock).mockResolvedValue(true);

      const newProduct = {
        product_type: type,
        product_name: `Test ${type}`,
        manufacturer: 'TestCorp',
        part_number: `TC-${type}-001`,
      };

      const response = await request(app).post('/api/products').send(newProduct);
      expect(response.status).toBe(201);
      expect(response.body.success).toBe(true);
    });
  });

  describe('DELETE /api/products/:id?type=<type>', () => {
    it.each(HARDWARE_TYPES)('deletes a %s product', async (type) => {
      (DynamoDBService.prototype.delete as jest.Mock).mockResolvedValue(true);

      const response = await request(app).delete(`/api/products/${type}-001?type=${type}`);
      expect(response.status).toBe(200);
      expect(response.body.success).toBe(true);
    });
  });
});

// =================== Search Route Per Type ===================

describe('Search route accepts all hardware product types', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it.each(HARDWARE_TYPES)('GET /api/v1/search?type=%s returns 200', async (type) => {
    (DynamoDBService.prototype.list as jest.Mock).mockResolvedValue([]);

    const response = await request(app).get(`/api/v1/search?type=${type}`);
    expect(response.status).toBe(200);
    expect(response.body.success).toBe(true);
  });

  it('rejects invalid product type', async () => {
    const response = await request(app).get('/api/v1/search?type=nonexistent');
    expect(response.status).toBe(400);
    expect(response.body.success).toBe(false);
  });
});

// =================== Summary Endpoint Per Type ===================

describe('Summary endpoint includes all product types', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('GET /api/products/summary returns counts object', async () => {
    const mockCounts = { total: 10, motors: 3, drives: 3, robot_arms: 2, gearheads: 2 };
    (DynamoDBService.prototype.count as jest.Mock).mockResolvedValue(mockCounts);

    const response = await request(app).get('/api/products/summary');
    expect(response.status).toBe(200);
    expect(response.body.data.total).toBe(10);
    expect(response.body.data.robot_arms).toBe(2);
    expect(response.body.data.gearheads).toBe(2);
  });
});
