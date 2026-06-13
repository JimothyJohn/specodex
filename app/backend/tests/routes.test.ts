/**
 * Tests for API routes
 */

import request from 'supertest';
import app from '../src/index';
import { DynamoDBService } from '../src/db/dynamodb';

// Mock DynamoDB service
jest.mock('../src/db/dynamodb');

describe('API Routes', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  // ===================== Health & Root =====================

  describe('GET /health', () => {
    it('should return health status', async () => {
      const response = await request(app).get('/health');
      expect(response.status).toBe(200);
      expect(response.body).toHaveProperty('status', 'healthy');
      expect(response.body).toHaveProperty('timestamp');
      expect(response.body).toHaveProperty('environment');
    });
  });

  describe('GET /', () => {
    it('should return API information', async () => {
      const response = await request(app).get('/');
      expect(response.status).toBe(200);
      expect(response.body).toHaveProperty('name', 'Specodex API');
      expect(response.body).toHaveProperty('version', '1.0.0');
      expect(response.body).toHaveProperty('endpoints');
      expect(response.body.endpoints).toHaveProperty('health');
      expect(response.body.endpoints).toHaveProperty('products');
    });
  });

  describe('404 handler', () => {
    it('should return 404 for unknown endpoints', async () => {
      const response = await request(app).get('/api/nonexistent');
      expect(response.status).toBe(404);
      expect(response.body.success).toBe(false);
      expect(response.body.error).toBe('Endpoint not found');
    });
  });

  // ===================== Products Summary =====================

  describe('GET /api/products/summary', () => {
    it('should return summary counts', async () => {
      const mockCounts = { total: 10, motors: 5, drives: 5 };
      (DynamoDBService.prototype.count as jest.Mock).mockResolvedValue(mockCounts);

      const response = await request(app).get('/api/products/summary');
      expect(response.status).toBe(200);
      expect(response.body.success).toBe(true);
      expect(response.body.data).toEqual(mockCounts);
    });

    it('should handle errors', async () => {
      (DynamoDBService.prototype.count as jest.Mock).mockRejectedValue(new Error('DB error'));

      const response = await request(app).get('/api/products/summary');
      expect(response.status).toBe(500);
      expect(response.body.success).toBe(false);
    });
  });

  // ===================== Categories =====================

  describe('GET /api/products/categories', () => {
    it('should return categories', async () => {
      const mockCategories = [
        { type: 'motor', count: 5, display_name: 'Motors' },
        { type: 'drive', count: 3, display_name: 'Drives' },
      ];
      (DynamoDBService.prototype.getCategories as jest.Mock).mockResolvedValue(mockCategories);

      const response = await request(app).get('/api/products/categories');
      expect(response.status).toBe(200);
      expect(response.body.success).toBe(true);
      expect(response.body.data).toHaveLength(2);
    });
  });

  // ===================== Manufacturers & Names =====================

  describe('GET /api/products/manufacturers', () => {
    it('should return manufacturers list', async () => {
      (DynamoDBService.prototype.getUniqueManufacturers as jest.Mock).mockResolvedValue(['ABB', 'Siemens']);

      const response = await request(app).get('/api/products/manufacturers');
      expect(response.status).toBe(200);
      expect(response.body.success).toBe(true);
      expect(response.body.data).toEqual(['ABB', 'Siemens']);
    });
  });

  describe('GET /api/products/names', () => {
    it('should return product names list', async () => {
      (DynamoDBService.prototype.getUniqueNames as jest.Mock).mockResolvedValue(['Motor A', 'Motor B']);

      const response = await request(app).get('/api/products/names');
      expect(response.status).toBe(200);
      expect(response.body.success).toBe(true);
      expect(response.body.data).toEqual(['Motor A', 'Motor B']);
    });
  });

  // ===================== List Products =====================

  describe('GET /api/products', () => {
    it('should list all products', async () => {
      const mockProducts = [
        { product_id: '1', product_type: 'motor', PK: 'PRODUCT#MOTOR', SK: 'PRODUCT#1' },
        { product_id: '2', product_type: 'drive', PK: 'PRODUCT#DRIVE', SK: 'PRODUCT#2' },
      ];
      (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({ items: mockProducts });

      const response = await request(app).get('/api/products');
      expect(response.status).toBe(200);
      expect(response.body.success).toBe(true);
      expect(response.body.count).toBe(2);
    });

    it('should filter by type', async () => {
      const mockMotors = [
        { product_id: '1', product_type: 'motor', PK: 'PRODUCT#MOTOR', SK: 'PRODUCT#1' },
      ];
      (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({ items: mockMotors });

      const response = await request(app).get('/api/products?type=motor');
      expect(response.status).toBe(200);
      expect(response.body.success).toBe(true);
      expect(response.body.count).toBe(1);
    });

    it('should pass limit parameter', async () => {
      (DynamoDBService.prototype.listPage as jest.Mock).mockResolvedValue({ items: [] });

      const response = await request(app).get('/api/products?type=motor&limit=5');
      expect(response.status).toBe(200);
      expect(DynamoDBService.prototype.listPage).toHaveBeenCalledWith('motor', 5, undefined);
    });
  });

  // ===================== Get Product by ID =====================

  describe('GET /api/products/:id', () => {
    it('should get product by id', async () => {
      const mockProduct = {
        product_id: '123',
        product_type: 'motor',
        PK: 'PRODUCT#MOTOR',
        SK: 'PRODUCT#123',
      };
      (DynamoDBService.prototype.read as jest.Mock).mockResolvedValue(mockProduct);

      const response = await request(app).get('/api/products/123?type=motor');
      expect(response.status).toBe(200);
      expect(response.body.success).toBe(true);
      expect(response.body.data.product_id).toBe('123');
    });

    it('should return 404 for non-existent product', async () => {
      (DynamoDBService.prototype.read as jest.Mock).mockResolvedValue(null);

      const response = await request(app).get('/api/products/999?type=motor');
      expect(response.status).toBe(404);
      expect(response.body.success).toBe(false);
    });

    it('should return 400 without type parameter', async () => {
      const response = await request(app).get('/api/products/123');
      expect(response.status).toBe(400);
      expect(response.body.error).toBe('type query parameter is required');
    });
  });

  // ===================== Create Products =====================

  describe('POST /api/products', () => {
    it('should create a single product', async () => {
      (DynamoDBService.prototype.create as jest.Mock).mockResolvedValue(true);

      const newProduct = {
        product_type: 'motor',
        product_name: 'Test Motor',
        manufacturer: 'Test Corp',
        part_number: 'TC-123',
      };

      const response = await request(app).post('/api/products').send(newProduct);
      expect(response.status).toBe(201);
      expect(response.body.success).toBe(true);
      expect(response.body.data.items_created).toBe(1);
    });

    it('should batch create multiple products', async () => {
      (DynamoDBService.prototype.batchCreate as jest.Mock).mockResolvedValue(2);

      const newProducts = [
        { product_type: 'motor', product_name: 'M1', manufacturer: 'Test Corp' },
        { product_type: 'drive', product_name: 'D1', manufacturer: 'Drive Inc' },
      ];

      const response = await request(app).post('/api/products').send(newProducts);
      expect(response.status).toBe(201);
      expect(response.body.success).toBe(true);
      expect(response.body.data.items_created).toBe(2);
      expect(response.body.data.items_received).toBe(2);
    });

    it('should return 400 when product_type is missing', async () => {
      const response = await request(app).post('/api/products').send({ manufacturer: 'Test' });
      expect(response.status).toBe(400);
      expect(response.body.error).toContain('product_type');
    });

    it('should auto-generate product_id', async () => {
      (DynamoDBService.prototype.create as jest.Mock).mockResolvedValue(true);

      const response = await request(app).post('/api/products').send({
        product_type: 'motor',
        product_name: 'Auto ID Motor',
        manufacturer: 'TestCorp',
      });

      expect(response.status).toBe(201);
    });
  });

  // ===================== Delete Products =====================

  describe('DELETE /api/products/:id', () => {
    it('should delete product', async () => {
      (DynamoDBService.prototype.delete as jest.Mock).mockResolvedValue(true);

      const response = await request(app).delete('/api/products/123?type=motor');
      expect(response.status).toBe(200);
      expect(response.body.success).toBe(true);
    });

    it('should return 404 for failed deletion', async () => {
      (DynamoDBService.prototype.delete as jest.Mock).mockResolvedValue(false);

      const response = await request(app).delete('/api/products/999?type=motor');
      expect(response.status).toBe(404);
    });

    it('should return 400 without type', async () => {
      const response = await request(app).delete('/api/products/123');
      expect(response.status).toBe(400);
    });
  });

  // ===================== Bulk Delete =====================

  describe('DELETE /api/products/part-number/:partNumber', () => {
    it('should delete by part number', async () => {
      (DynamoDBService.prototype.deleteByPartNumber as jest.Mock).mockResolvedValue({ deleted: 2, failed: 0 });

      const response = await request(app).delete('/api/products/part-number/TC-123');
      expect(response.status).toBe(200);
      expect(response.body.data.deleted).toBe(2);
    });

    it('should return 404 when no products found', async () => {
      (DynamoDBService.prototype.deleteByPartNumber as jest.Mock).mockResolvedValue({ deleted: 0, failed: 0 });

      const response = await request(app).delete('/api/products/part-number/NONEXISTENT');
      expect(response.status).toBe(404);
    });
  });

  describe('DELETE /api/products/manufacturer/:manufacturer', () => {
    it('should delete by manufacturer', async () => {
      (DynamoDBService.prototype.deleteByManufacturer as jest.Mock).mockResolvedValue({ deleted: 3, failed: 0 });

      const response = await request(app).delete('/api/products/manufacturer/TestCorp');
      expect(response.status).toBe(200);
      expect(response.body.data.deleted).toBe(3);
    });

    it('should return 404 when no products found', async () => {
      (DynamoDBService.prototype.deleteByManufacturer as jest.Mock).mockResolvedValue({ deleted: 0, failed: 0 });

      const response = await request(app).delete('/api/products/manufacturer/Unknown');
      expect(response.status).toBe(404);
    });
  });

  describe('DELETE /api/products/name/:name', () => {
    it('should delete by product name', async () => {
      (DynamoDBService.prototype.deleteByProductName as jest.Mock).mockResolvedValue({ deleted: 1, failed: 0 });

      const response = await request(app).delete('/api/products/name/TestMotor');
      expect(response.status).toBe(200);
      expect(response.body.data.deleted).toBe(1);
    });
  });

  // ===================== Deduplicate =====================

  describe('POST /api/products/deduplicate', () => {
    it('should perform dry run', async () => {
      const mockProducts = [
        { product_id: '1', product_type: 'motor', part_number: 'X', product_name: 'M', manufacturer: 'A', PK: 'P#M', SK: 'P#1' },
        { product_id: '2', product_type: 'motor', part_number: 'X', product_name: 'M', manufacturer: 'A', PK: 'P#M', SK: 'P#2' },
      ];
      (DynamoDBService.prototype.listAll as jest.Mock).mockResolvedValue(mockProducts);

      const response = await request(app).post('/api/products/deduplicate').send({ confirm: false });
      expect(response.status).toBe(200);
      expect(response.body.data.dry_run).toBe(true);
      expect(response.body.data.found).toBe(1);
    });

    it('should delete when confirmed', async () => {
      const mockProducts = [
        { product_id: '1', product_type: 'motor', part_number: 'X', product_name: 'M', manufacturer: 'A', PK: 'P#M', SK: 'P#1' },
        { product_id: '2', product_type: 'motor', part_number: 'X', product_name: 'M', manufacturer: 'A', PK: 'P#M', SK: 'P#2' },
      ];
      (DynamoDBService.prototype.listAll as jest.Mock).mockResolvedValue(mockProducts);
      (DynamoDBService.prototype.batchDelete as jest.Mock).mockResolvedValue(1);

      const response = await request(app).post('/api/products/deduplicate').send({ confirm: true });
      expect(response.status).toBe(200);
      expect(response.body.data.dry_run).toBe(false);
      expect(response.body.data.deleted).toBe(1);
    });
  });

  // ===================== Datasheets =====================

  describe('GET /api/datasheets', () => {
    it('should list datasheets', async () => {
      const mockDatasheets = [
        { datasheet_id: 'd1', url: 'https://example.com/a.pdf', product_type: 'motor', product_name: 'M1' },
      ];
      (DynamoDBService.prototype.listDatasheets as jest.Mock).mockResolvedValue(mockDatasheets);

      const response = await request(app).get('/api/datasheets');
      expect(response.status).toBe(200);
      expect(response.body.success).toBe(true);
      expect(response.body.data).toHaveLength(1);
      // Should map product_type to 'datasheet' and preserve component_type
      expect(response.body.data[0].product_type).toBe('datasheet');
      expect(response.body.data[0].component_type).toBe('motor');
    });
  });

  describe('POST /api/datasheets', () => {
    it('should create a datasheet', async () => {
      (DynamoDBService.prototype.datasheetExists as jest.Mock).mockResolvedValue(false);
      (DynamoDBService.prototype.create as jest.Mock).mockResolvedValue(true);

      const response = await request(app).post('/api/datasheets').send({
        url: 'https://example.com/test.pdf',
        product_type: 'motor',
        product_name: 'Test Motor',
      });

      expect(response.status).toBe(201);
      expect(response.body.success).toBe(true);
    });

    it('should return 409 for duplicate URL', async () => {
      (DynamoDBService.prototype.datasheetExists as jest.Mock).mockResolvedValue(true);

      const response = await request(app).post('/api/datasheets').send({
        url: 'https://example.com/existing.pdf',
        product_type: 'motor',
        product_name: 'Test Motor',
      });

      expect(response.status).toBe(409);
    });

    it('should return 400 for missing fields', async () => {
      const response = await request(app).post('/api/datasheets').send({
        url: 'https://example.com/test.pdf',
      });

      expect(response.status).toBe(400);
      expect(response.body.error).toContain('Missing required fields');
    });
  });

  describe('DELETE /api/datasheets/:id', () => {
    it('should delete a datasheet', async () => {
      (DynamoDBService.prototype.deleteDatasheet as jest.Mock).mockResolvedValue(true);

      const response = await request(app).delete('/api/datasheets/d1?type=motor');
      expect(response.status).toBe(200);
      expect(response.body.success).toBe(true);
    });

    it('should return 400 without type', async () => {
      const response = await request(app).delete('/api/datasheets/d1');
      expect(response.status).toBe(400);
    });

    it('should return 404 for non-existent datasheet', async () => {
      (DynamoDBService.prototype.deleteDatasheet as jest.Mock).mockResolvedValue(false);

      const response = await request(app).delete('/api/datasheets/nonexistent?type=motor');
      expect(response.status).toBe(404);
    });
  });

  describe('PUT /api/datasheets/:id', () => {
    it('should update a datasheet', async () => {
      (DynamoDBService.prototype.updateDatasheet as jest.Mock).mockResolvedValue(true);

      const response = await request(app).put('/api/datasheets/d1').send({
        product_type: 'motor',
        product_name: 'Updated Motor',
      });

      expect(response.status).toBe(200);
      expect(response.body.success).toBe(true);
    });

    it('should return 400 without product_type', async () => {
      const response = await request(app).put('/api/datasheets/d1').send({
        product_name: 'Updated',
      });

      expect(response.status).toBe(400);
    });

    it('should return 404 for non-existent datasheet', async () => {
      (DynamoDBService.prototype.updateDatasheet as jest.Mock).mockResolvedValue(false);

      const response = await request(app).put('/api/datasheets/nonexistent').send({
        product_type: 'motor',
      });

      expect(response.status).toBe(404);
    });
  });
});
