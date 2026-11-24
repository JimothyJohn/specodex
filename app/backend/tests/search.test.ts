/**
 * Tests for search service and search/docs routes.
 */

import request from 'supertest';
import app from '../src/index';
import { DynamoDBService } from '../src/db/dynamodb';
import {
  extractNumeric,
  textScore,
  parseWhere,
  parseSort,
  applyWhere,
  sortProducts,
  searchProducts,
  productSummary,
  getProductField,
} from '../src/services/search';
import { Product } from '../src/types/models';

jest.mock('../src/db/dynamodb');

// Shared test fixtures
const mockMotor: Product = {
  product_id: 'abc-123',
  product_type: 'motor',
  manufacturer: 'Maxon',
  product_name: 'EC-45 flat',
  part_number: '397172',
  PK: 'PRODUCT#MOTOR',
  SK: 'PRODUCT#abc-123',
  type: 'brushless dc',
  series: 'EC-i',
  rated_power: { value: 100, unit: 'W' },
  rated_voltage: { min: 24, max: 48, unit: 'V' },
  rated_speed: { value: 3000, unit: 'rpm' },
  rated_torque: { value: 0.5, unit: 'Nm' },
  rated_current: { value: 4.2, unit: 'A' },
} as unknown as Product;

const mockMotor2: Product = {
  product_id: 'def-456',
  product_type: 'motor',
  manufacturer: 'Siemens',
  product_name: 'SIMOTICS S-1FK7',
  part_number: '1FK7042',
  PK: 'PRODUCT#MOTOR',
  SK: 'PRODUCT#def-456',
  type: 'ac servo',
  rated_power: { value: 200, unit: 'W' },
  rated_voltage: { min: 230, max: 400, unit: 'V' },
  rated_speed: { value: 6000, unit: 'rpm' },
  rated_torque: { value: 1.2, unit: 'Nm' },
} as unknown as Product;

const mockDrive: Product = {
  product_id: 'ghi-789',
  product_type: 'drive',
  manufacturer: 'ABB',
  product_name: 'ACS580',
  PK: 'PRODUCT#DRIVE',
  SK: 'PRODUCT#ghi-789',
  type: 'variable frequency',
  rated_power: { value: 500, unit: 'W' },
  rated_current: { value: 10, unit: 'A' },
} as unknown as Product;

// =================== Search Service Unit Tests ===================

describe('Search Service', () => {
  describe('extractNumeric', () => {
    it('returns value from ValueUnit', () => {
      expect(extractNumeric({ value: 24, unit: 'V' })).toBe(24);
    });

    it('returns min from MinMaxUnit', () => {
      expect(extractNumeric({ min: 24, max: 48, unit: 'V' })).toBe(24);
    });

    it('returns plain numbers', () => {
      expect(extractNumeric(42)).toBe(42);
      expect(extractNumeric(3.14)).toBe(3.14);
    });

    it('parses numeric strings', () => {
      expect(extractNumeric('100')).toBe(100);
    });

    it('returns null for non-numeric values', () => {
      expect(extractNumeric(null)).toBeNull();
      expect(extractNumeric(undefined)).toBeNull();
      expect(extractNumeric('hello')).toBeNull();
    });
  });

  describe('getProductField', () => {
    it('accesses existing fields', () => {
      expect(getProductField(mockMotor, 'manufacturer')).toBe('Maxon');
    });

    it('returns undefined for missing fields', () => {
      expect(getProductField(mockMotor, 'nonexistent')).toBeUndefined();
    });
  });

  describe('textScore', () => {
    it('scores exact part_number match at 100', () => {
      expect(textScore(mockMotor, '397172')).toBe(100);
    });

    it('scores substring product_name match at 70', () => {
      expect(textScore(mockMotor, 'EC-45')).toBe(70);
    });

    it('scores exact manufacturer match at 85', () => {
      expect(textScore(mockMotor, 'maxon')).toBe(85);
    });

    it('is case-insensitive', () => {
      expect(textScore(mockMotor, 'MAXON')).toBe(85);
      expect(textScore(mockMotor, 'ec-45')).toBe(70);
    });

    it('scores 0 for no match', () => {
      expect(textScore(mockMotor, 'nonexistent-xyz')).toBe(0);
    });

    it('scores query-contains-value match', () => {
      // "Maxon EC-45 flat" query contains "EC-i" (series)
      expect(textScore(mockMotor, 'EC-i motor series')).toBeGreaterThan(0);
    });
  });

  describe('parseWhere', () => {
    it('parses >= operator', () => {
      expect(parseWhere('rated_power>=100')).toEqual({
        field: 'rated_power', op: '>=', value: '100',
      });
    });

    it('parses <= operator', () => {
      expect(parseWhere('rated_voltage<=48')).toEqual({
        field: 'rated_voltage', op: '<=', value: '48',
      });
    });

    it('parses > operator', () => {
      expect(parseWhere('poles>4')).toEqual({
        field: 'poles', op: '>', value: '4',
      });
    });

    it('parses < operator', () => {
      expect(parseWhere('weight<5')).toEqual({
        field: 'weight', op: '<', value: '5',
      });
    });

    it('parses = operator', () => {
      expect(parseWhere('type=brushless')).toEqual({
        field: 'type', op: '=', value: 'brushless',
      });
    });

    it('parses != operator', () => {
      expect(parseWhere('type!=servo')).toEqual({
        field: 'type', op: '!=', value: 'servo',
      });
    });

    it('throws on invalid expression', () => {
      expect(() => parseWhere('invalid')).toThrow('Cannot parse filter');
    });
  });

  describe('parseSort', () => {
    it('parses field:desc', () => {
      expect(parseSort('rated_power:desc')).toEqual({
        field: 'rated_power', reverse: true,
      });
    });

    it('parses field:asc', () => {
      expect(parseSort('weight:asc')).toEqual({
        field: 'weight', reverse: false,
      });
    });

    it('defaults to ascending', () => {
      expect(parseSort('rated_power')).toEqual({
        field: 'rated_power', reverse: false,
      });
    });
  });

  describe('applyWhere', () => {
    it('numeric >= comparison on ValueUnit', () => {
      expect(applyWhere(mockMotor, 'rated_power', '>=', '100')).toBe(true);
      expect(applyWhere(mockMotor, 'rated_power', '>=', '200')).toBe(false);
    });

    it('numeric < comparison on MinMaxUnit (uses min)', () => {
      // rated_voltage: {min: 24, max: 48} => extractNumeric returns 24
      expect(applyWhere(mockMotor, 'rated_voltage', '<', '48')).toBe(true);
      expect(applyWhere(mockMotor, 'rated_voltage', '<', '20')).toBe(false);
    });

    it('string = uses substring match', () => {
      expect(applyWhere(mockMotor, 'type', '=', 'brushless')).toBe(true);
      expect(applyWhere(mockMotor, 'type', '=', 'servo')).toBe(false);
    });

    it('string != uses substring exclusion', () => {
      expect(applyWhere(mockMotor, 'type', '!=', 'servo')).toBe(true);
      expect(applyWhere(mockMotor, 'type', '!=', 'brushless')).toBe(false);
    });

    it('returns false for missing field', () => {
      expect(applyWhere(mockMotor, 'nonexistent', '>=', '0')).toBe(false);
    });
  });

  describe('sortProducts', () => {
    const products = [mockMotor, mockMotor2];

    it('sorts ascending by default', () => {
      const sorted = sortProducts(products, ['rated_power']);
      expect(extractNumeric(getProductField(sorted[0], 'rated_power'))).toBe(100);
      expect(extractNumeric(getProductField(sorted[1], 'rated_power'))).toBe(200);
    });

    it('sorts descending', () => {
      const sorted = sortProducts(products, ['rated_power:desc']);
      expect(extractNumeric(getProductField(sorted[0], 'rated_power'))).toBe(200);
      expect(extractNumeric(getProductField(sorted[1], 'rated_power'))).toBe(100);
    });

    it('sorts nulls last', () => {
      const withNull = { ...mockMotor, product_id: 'null-test', rated_power: undefined } as unknown as Product;
      const sorted = sortProducts([withNull, mockMotor2], ['rated_power']);
      expect(getProductField(sorted[0], 'rated_power')).toBeDefined();
      expect(getProductField(sorted[1], 'rated_power')).toBeUndefined();
    });

    it('handles multi-level sort', () => {
      const m1 = { ...mockMotor, manufacturer: 'AAA', rated_power: { value: 200, unit: 'W' } } as unknown as Product;
      const m2 = { ...mockMotor2, manufacturer: 'AAA', rated_power: { value: 100, unit: 'W' } } as unknown as Product;
      const m3 = { ...mockMotor, manufacturer: 'BBB', rated_power: { value: 150, unit: 'W' } } as unknown as Product;
      const sorted = sortProducts([m3, m1, m2], ['manufacturer', 'rated_power:desc']);
      expect(getProductField(sorted[0], 'manufacturer')).toBe('AAA');
      expect(extractNumeric(getProductField(sorted[0], 'rated_power'))).toBe(200);
      expect(getProductField(sorted[2], 'manufacturer')).toBe('BBB');
    });
  });

  describe('productSummary', () => {
    it('includes identification fields', () => {
      const summary = productSummary(mockMotor);
      expect(summary.product_id).toBe('abc-123');
      expect(summary.product_type).toBe('motor');
      expect(summary.manufacturer).toBe('Maxon');
      expect(summary.product_name).toBe('EC-45 flat');
    });

    it('includes key motor specs', () => {
      const summary = productSummary(mockMotor);
      expect(summary.rated_power).toEqual({ value: 100, unit: 'W' });
      expect(summary.rated_speed).toEqual({ value: 3000, unit: 'rpm' });
    });

    it('includes key inductive sensor specs', () => {
      const sensor = {
        product_id: 'sns-1',
        product_type: 'inductive_sensor',
        manufacturer: 'SICK',
        product_name: 'IMB18',
        part_number: 'IMB18-08BPOVC0K',
        housing_size: 'M18',
        sensing_distance: { value: 8, unit: 'mm' },
        mounting: 'flush',
        output_type: 'pnp',
        ip_rating: 68,
      } as unknown as Product;
      const summary = productSummary(sensor);
      expect(summary.housing_size).toBe('M18');
      expect(summary.sensing_distance).toEqual({ value: 8, unit: 'mm' });
      expect(summary.mounting).toBe('flush');
      expect(summary.output_type).toBe('pnp');
      expect(summary.ip_rating).toBe(68);
    });

    it('includes relevance when provided', () => {
      const summary = productSummary(mockMotor, 90);
      expect(summary.relevance).toBe(90);
    });

    it('omits relevance when 0 or undefined', () => {
      expect(productSummary(mockMotor).relevance).toBeUndefined();
      expect(productSummary(mockMotor, 0).relevance).toBeUndefined();
    });
  });

  describe('searchProducts', () => {
    const products = [mockMotor, mockMotor2, mockDrive];

    it('text-only search returns scored results', () => {
      const result = searchProducts({ products, query: 'EC-45' });
      expect(result.count).toBeGreaterThan(0);
      expect(result.products[0].relevance).toBeGreaterThan(0);
      expect(result.products[0].product_name).toBe('EC-45 flat');
    });

    it('filter-only returns matching products', () => {
      // rated_torque is motor-specific; rated_power is now shared with drives
      // after the rename from output_power, so we filter on the motor-only spec.
      const result = searchProducts({
        products,
        where: ['rated_torque>=1.0'],
      });
      expect(result.count).toBe(1);
      expect(result.products[0].manufacturer).toBe('Siemens');
    });

    it('manufacturer filter works', () => {
      const result = searchProducts({ products, manufacturer: 'ABB' });
      expect(result.count).toBe(1);
      expect(result.products[0].manufacturer).toBe('ABB');
    });

    it('combined search + filter + sort', () => {
      const result = searchProducts({
        products: [mockMotor, mockMotor2],
        where: ['rated_power>=50'],
        sort: ['rated_power:desc'],
      });
      expect(result.count).toBe(2);
      expect(result.products[0].manufacturer).toBe('Siemens');
    });

    it('respects limit', () => {
      const result = searchProducts({ products, limit: 1 });
      expect(result.count).toBe(1);
    });

    it('clamps limit to 100', () => {
      const result = searchProducts({ products, limit: 999 });
      expect(result.count).toBeLessThanOrEqual(100);
    });

    it('returns empty on no text match', () => {
      const result = searchProducts({ products, query: 'nonexistent-xyz-999' });
      expect(result.count).toBe(0);
    });
  });
});

// =================== Search Route Integration Tests ===================

describe('Search Route', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  describe('GET /api/v1/search', () => {
    it('returns results for text query', async () => {
      (DynamoDBService.prototype.list as jest.Mock).mockResolvedValue([mockMotor, mockMotor2]);

      const response = await request(app).get('/api/v1/search?q=EC-45');
      expect(response.status).toBe(200);
      expect(response.body.success).toBe(true);
      expect(response.body.count).toBeGreaterThan(0);
      expect(response.body.data[0].product_name).toBe('EC-45 flat');
    });

    it('filters by type', async () => {
      (DynamoDBService.prototype.list as jest.Mock).mockResolvedValue([mockMotor]);

      const response = await request(app).get('/api/v1/search?type=motor');
      expect(response.status).toBe(200);
      expect(DynamoDBService.prototype.list).toHaveBeenCalledWith('motor');
    });

    it('applies where filter', async () => {
      (DynamoDBService.prototype.list as jest.Mock).mockResolvedValue([mockMotor, mockMotor2]);

      const response = await request(app).get('/api/v1/search?where=rated_power>=200');
      expect(response.status).toBe(200);
      expect(response.body.count).toBe(1);
    });

    it('applies sort', async () => {
      (DynamoDBService.prototype.list as jest.Mock).mockResolvedValue([mockMotor, mockMotor2]);

      const response = await request(app).get('/api/v1/search?sort=rated_power:desc');
      expect(response.status).toBe(200);
      expect(response.body.data[0].manufacturer).toBe('Siemens');
    });

    it('returns all products with no params', async () => {
      (DynamoDBService.prototype.list as jest.Mock).mockResolvedValue([mockMotor]);

      const response = await request(app).get('/api/v1/search');
      expect(response.status).toBe(200);
      expect(response.body.success).toBe(true);
      expect(response.body.count).toBeGreaterThan(0);
    });

    it('returns 400 for invalid type', async () => {
      const response = await request(app).get('/api/v1/search?type=invalid');
      expect(response.status).toBe(400);
      expect(response.body.success).toBe(false);
    });

    it('handles DB errors gracefully', async () => {
      (DynamoDBService.prototype.list as jest.Mock).mockRejectedValue(new Error('DB error'));

      const response = await request(app).get('/api/v1/search?type=motor');
      expect(response.status).toBe(500);
      expect(response.body.success).toBe(false);
    });
  });
});

// =================== Docs Route Tests ===================

describe('Docs Routes', () => {
  describe('GET /api/openapi.json', () => {
    it('returns valid OpenAPI spec', async () => {
      const response = await request(app).get('/api/openapi.json');
      expect(response.status).toBe(200);
      expect(response.headers['content-type']).toMatch(/json/);
      expect(response.body.openapi).toBe('3.1.0');
      expect(response.body.info.title).toBe('Specodex API');
    });

    it('contains all expected paths', async () => {
      const response = await request(app).get('/api/openapi.json');
      const paths = Object.keys(response.body.paths);
      expect(paths).toContain('/health');
      expect(paths).toContain('/api/v1/search');
      expect(paths).toContain('/api/products');
      expect(paths).toContain('/api/products/{id}');
      expect(paths).toContain('/api/products/summary');
      expect(paths).toContain('/api/products/categories');
      expect(paths).toContain('/api/products/manufacturers');
      expect(paths).toContain('/api/products/names');
    });

    it('contains component schemas', async () => {
      const response = await request(app).get('/api/openapi.json');
      const schemas = Object.keys(response.body.components.schemas);
      expect(schemas).toContain('ValueUnit');
      expect(schemas).toContain('MinMaxUnit');
      expect(schemas).toContain('Motor');
      expect(schemas).toContain('Drive');
      expect(schemas).toContain('ProductSummary');
      expect(schemas).toContain('ErrorResponse');
    });
  });

  describe('GET /api/docs', () => {
    it('returns HTML docs page', async () => {
      const response = await request(app).get('/api/docs');
      expect(response.status).toBe(200);
      expect(response.headers['content-type']).toMatch(/html/);
      expect(response.text).toContain('<script');
      expect(response.text).toContain('api-reference');
      expect(response.text).toContain('openapi.json');
    });
  });
});

// =================== Root Endpoint Updated ===================

describe('Root endpoint updates', () => {
  it('includes new endpoints in root response', async () => {
    const response = await request(app).get('/');
    expect(response.body.endpoints.search).toBe('/api/v1/search');
    expect(response.body.endpoints.openapi).toBe('/api/openapi.json');
    expect(response.body.endpoints.docs).toBe('/api/docs');
  });
});
