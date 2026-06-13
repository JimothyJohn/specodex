/**
 * Tests for DynamoDB service
 */

import { DynamoDBService } from '../src/db/dynamodb';
import { Motor, Drive, Datasheet, Product } from '../src/types/models';

// Mock AWS SDK
jest.mock('@aws-sdk/client-dynamodb');
jest.mock('@aws-sdk/util-dynamodb', () => ({
  marshall: jest.fn((obj) => obj),
  unmarshall: jest.fn((obj) => obj),
}));

describe('DynamoDBService', () => {
  let db: DynamoDBService;

  beforeEach(() => {
    jest.clearAllMocks();
    db = new DynamoDBService({ tableName: 'test-table' });
  });

  // ===================== Serialization =====================

  describe('serializeItem', () => {
    it('should add correct PK and SK for a motor', () => {
      const motor: Partial<Motor> = {
        product_id: 'test-123',
        product_type: 'motor',
        manufacturer: 'Test Corp',
        part_number: 'TC-123',
      };

      const serialized = (db as any).serializeItem(motor);
      expect(serialized.PK).toBe('PRODUCT#MOTOR');
      expect(serialized.SK).toBe('PRODUCT#test-123');
    });

    it('should add correct PK and SK for a drive', () => {
      const drive: Partial<Drive> = {
        product_id: 'drive-456',
        product_type: 'drive',
        manufacturer: 'Drive Inc',
      };

      const serialized = (db as any).serializeItem(drive);
      expect(serialized.PK).toBe('PRODUCT#DRIVE');
      expect(serialized.SK).toBe('PRODUCT#drive-456');
    });

    it('should add correct PK and SK for a datasheet', () => {
      const datasheet: Partial<Datasheet> = {
        datasheet_id: 'ds-789',
        url: 'https://example.com/test.pdf',
        product_type: 'motor',
        product_name: 'Test',
      };

      const serialized = (db as any).serializeItem(datasheet);
      expect(serialized.PK).toBe('DATASHEET#MOTOR');
      expect(serialized.SK).toBe('DATASHEET#ds-789');
    });
  });

  describe('deserializeProduct', () => {
    it('should pass through normal products', () => {
      const item = {
        product_id: '123',
        product_type: 'motor',
        manufacturer: 'Test',
      };

      const result = (db as any).deserializeProduct(item);
      expect(result.product_id).toBe('123');
    });

    it('should map datasheet_id to product_id for datasheets', () => {
      const item = {
        datasheet_id: 'ds-123',
        product_type: 'datasheet',
        url: 'https://example.com/test.pdf',
      };

      const result = (db as any).deserializeProduct(item);
      expect(result.product_id).toBe('ds-123');
    });
  });

  // ===================== CRUD Operations =====================

  describe('create', () => {
    it('should send PutItemCommand', async () => {
      const mockSend = jest.fn().mockResolvedValue({});
      (db as any).client = { send: mockSend };

      const product: Partial<Motor> = {
        product_id: '123',
        product_type: 'motor',
        manufacturer: 'Test',
      };

      const result = await db.create(product as Product);
      expect(result).toBe(true);
      expect(mockSend).toHaveBeenCalledTimes(1);
    });

    it('should return false on error', async () => {
      const mockSend = jest.fn().mockRejectedValue(new Error('DB error'));
      (db as any).client = { send: mockSend };

      const result = await db.create({ product_type: 'motor' } as Product);
      expect(result).toBe(false);
    });
  });

  describe('read', () => {
    it('should return product when found', async () => {
      const mockItem = {
        product_id: '123',
        product_type: 'motor',
      };
      const mockSend = jest.fn().mockResolvedValue({ Item: mockItem });
      (db as any).client = { send: mockSend };

      const result = await db.read('123', 'motor');
      expect(result).toBeDefined();
      expect(result?.product_type).toBe('motor');
    });

    it('should return null when not found', async () => {
      const mockSend = jest.fn().mockResolvedValue({});
      (db as any).client = { send: mockSend };

      const result = await db.read('nonexistent', 'motor');
      expect(result).toBeNull();
    });

    it('should return null on error', async () => {
      const mockSend = jest.fn().mockRejectedValue(new Error('DB error'));
      (db as any).client = { send: mockSend };

      const result = await db.read('123', 'motor');
      expect(result).toBeNull();
    });
  });

  describe('delete', () => {
    it('should send DeleteItemCommand', async () => {
      const mockSend = jest.fn().mockResolvedValue({});
      (db as any).client = { send: mockSend };

      const result = await db.delete('123', 'motor');
      expect(result).toBe(true);
      expect(mockSend).toHaveBeenCalledTimes(1);
    });

    it('should return false on error', async () => {
      const mockSend = jest.fn().mockRejectedValue(new Error('DB error'));
      (db as any).client = { send: mockSend };

      const result = await db.delete('123', 'motor');
      expect(result).toBe(false);
    });
  });

  // ===================== List & Pagination =====================

  describe('list', () => {
    it('should return products for a type', async () => {
      const mockSend = jest.fn().mockResolvedValue({
        Items: [
          { product_id: '1', product_type: 'motor' },
          { product_id: '2', product_type: 'motor' },
        ],
      });
      (db as any).client = { send: mockSend };

      const result = await db.list('motor');
      expect(result).toHaveLength(2);
    });

    it('should handle pagination', async () => {
      const mockSend = jest.fn()
        .mockResolvedValueOnce({
          Items: [{ product_id: '1', product_type: 'motor' }],
          LastEvaluatedKey: { PK: 'x', SK: 'y' },
        })
        .mockResolvedValueOnce({
          Items: [{ product_id: '2', product_type: 'motor' }],
        });
      (db as any).client = { send: mockSend };

      const result = await db.list('motor');
      expect(result).toHaveLength(2);
      expect(mockSend).toHaveBeenCalledTimes(2);
    });

    it('should return empty array on error', async () => {
      const mockSend = jest.fn().mockRejectedValue(new Error('DB error'));
      (db as any).client = { send: mockSend };

      const result = await db.list('motor');
      expect(result).toEqual([]);
    });
  });

  // ===================== Batch Operations =====================

  describe('batchCreate', () => {
    it('should create items in batches of 25', async () => {
      const mockSend = jest.fn().mockResolvedValue({});
      (db as any).client = { send: mockSend };

      // 30 products = 2 batches (25 + 5)
      const products: Product[] = Array.from({ length: 30 }, (_, i) => ({
        product_id: `id-${i}`,
        product_type: 'motor' as const,
        manufacturer: 'TestCorp',
        PK: 'PRODUCT#MOTOR',
        SK: `PRODUCT#id-${i}`,
      }));

      const result = await db.batchCreate(products);
      expect(result).toBe(30);
      expect(mockSend).toHaveBeenCalledTimes(2);
    });

    it('should return 0 for empty array', async () => {
      const result = await db.batchCreate([]);
      expect(result).toBe(0);
    });
  });

  describe('batchDelete', () => {
    it('should delete items in batches', async () => {
      const mockSend = jest.fn().mockResolvedValue({});
      (db as any).client = { send: mockSend };

      const items = [
        { PK: 'P#M', SK: 'P#1' },
        { PK: 'P#M', SK: 'P#2' },
      ];

      const result = await db.batchDelete(items);
      expect(result).toBe(2);
    });

    it('should return 0 for empty array', async () => {
      const result = await db.batchDelete([]);
      expect(result).toBe(0);
    });
  });

  // ===================== Count =====================

  describe('count', () => {
    it('should return counts per type', async () => {
      // count() uses the private countByType helper, which under the
      // hood issues a Query with Select=COUNT. Spy on the helper to
      // avoid coupling the test to the AWS SDK command shape.
      jest
        .spyOn(db as any, 'countByType')
        .mockImplementation(async (...args: unknown[]) => {
          const type = args[0] as string;
          if (type === 'motor') return 1;
          if (type === 'drive') return 2;
          return 0;
        });

      const counts = await db.count();
      expect(counts.total).toBeGreaterThanOrEqual(3);
      expect(counts.motors).toBe(1);
      expect(counts.drives).toBe(2);
    });
  });

  // ===================== Datasheet Operations =====================

  describe('datasheetExists', () => {
    it('should return true when datasheet found', async () => {
      const mockSend = jest.fn().mockResolvedValue({
        Items: [{ PK: 'DATASHEET#MOTOR' }],
      });
      (db as any).client = { send: mockSend };

      const result = await db.datasheetExists('https://example.com/test.pdf');
      expect(result).toBe(true);
    });

    it('should return false when not found', async () => {
      const mockSend = jest.fn().mockResolvedValue({ Items: [] });
      (db as any).client = { send: mockSend };

      const result = await db.datasheetExists('https://example.com/nonexistent.pdf');
      expect(result).toBe(false);
    });

    it('should return false on error', async () => {
      const mockSend = jest.fn().mockRejectedValue(new Error('DB error'));
      (db as any).client = { send: mockSend };

      const result = await db.datasheetExists('https://example.com/test.pdf');
      expect(result).toBe(false);
    });
  });

  describe('listDatasheets', () => {
    it('should return datasheets', async () => {
      const mockSend = jest.fn().mockResolvedValue({
        Items: [
          { datasheet_id: 'd1', url: 'https://example.com/a.pdf', product_type: 'motor' },
        ],
      });
      (db as any).client = { send: mockSend };

      const result = await db.listDatasheets();
      expect(result).toHaveLength(1);
    });
  });

  describe('deleteDatasheet', () => {
    it('should delete and return true', async () => {
      const mockSend = jest.fn().mockResolvedValue({});
      (db as any).client = { send: mockSend };

      const result = await db.deleteDatasheet('d1', 'motor');
      expect(result).toBe(true);
    });
  });

  describe('updateDatasheet', () => {
    it('should update existing datasheet', async () => {
      const existingItem = {
        datasheet_id: 'd1',
        product_type: 'motor',
        product_name: 'Old Name',
        url: 'https://example.com/test.pdf',
      };
      const mockSend = jest.fn()
        .mockResolvedValueOnce({ Item: existingItem }) // GetItem
        .mockResolvedValueOnce({}); // PutItem

      (db as any).client = { send: mockSend };

      const result = await db.updateDatasheet('d1', 'motor', { product_name: 'New Name' });
      expect(result).toBe(true);
      expect(mockSend).toHaveBeenCalledTimes(2);
    });

    it('should return false when datasheet not found', async () => {
      const mockSend = jest.fn().mockResolvedValue({}); // No Item
      (db as any).client = { send: mockSend };

      const result = await db.updateDatasheet('nonexistent', 'motor', {});
      expect(result).toBe(false);
    });
  });

  // ===================== Unique Values =====================

  describe('getUniqueManufacturers', () => {
    it('should return sorted unique manufacturers (projection-only scan)', async () => {
      // The implementation issues one Query per product type with
      // ProjectionExpression='manufacturer'. Return a row on the first
      // call, an empty page on every subsequent call so the result is
      // deterministic regardless of VALID_PRODUCT_TYPES order/length.
      let call = 0;
      const mockSend = jest.fn().mockImplementation(async () => {
        call += 1;
        if (call === 1) {
          return {
            Items: [
              { manufacturer: 'ABB' },
              { manufacturer: 'Siemens' },
              { manufacturer: 'ABB' },
            ],
          };
        }
        return { Items: [] };
      });
      (db as any).client = { send: mockSend };

      const result = await db.getUniqueManufacturers();
      expect(result).toEqual(['ABB', 'Siemens']);
      // One Query per product type — verify we scan each partition.
      expect(mockSend.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
  });

  describe('getUniqueNames', () => {
    it('should return sorted unique names (projection-only scan)', async () => {
      let call = 0;
      const mockSend = jest.fn().mockImplementation(async () => {
        call += 1;
        if (call === 1) {
          return {
            Items: [
              { product_name: 'Motor B' },
              { product_name: 'Motor A' },
              { product_name: 'Motor B' },
            ],
          };
        }
        return { Items: [] };
      });
      (db as any).client = { send: mockSend };

      const result = await db.getUniqueNames();
      expect(result).toEqual(['Motor A', 'Motor B']);
    });
  });

  // ===================== listPage (cursor pagination) =====================

  describe('listPage', () => {
    const row = (id: string) => ({
      product_id: id,
      product_type: 'motor',
      manufacturer: 'X',
    });

    it('returns items with no next when the type fits in one DynamoDB page', async () => {
      const mockSend = jest
        .fn()
        .mockResolvedValue({ Items: [row('p1'), row('p2')] });
      (db as any).client = { send: mockSend };

      const page = await db.listPage('motor', 10);
      expect(page.items).toHaveLength(2);
      expect(page.next).toBeUndefined();
      expect(mockSend).toHaveBeenCalledTimes(1);
    });

    it('returns next {type, key} when the limit is hit with rows remaining', async () => {
      const lek = { PK: { S: 'PRODUCT#MOTOR' }, SK: { S: 'PRODUCT#p2' } };
      const mockSend = jest
        .fn()
        .mockResolvedValue({ Items: [row('p1'), row('p2')], LastEvaluatedKey: lek });
      (db as any).client = { send: mockSend };

      const page = await db.listPage('motor', 2);
      expect(page.items).toHaveLength(2);
      expect(page.next).toEqual({ type: 'motor', key: lek });
    });

    it('keeps querying within a type when DynamoDB pages are short of the limit', async () => {
      // DynamoDB's Limit caps rows *scanned* per request, so a page can
      // come back short with a LastEvaluatedKey; listPage must loop.
      const lek = { SK: { S: 'PRODUCT#p1' } };
      const mockSend = jest
        .fn()
        .mockResolvedValueOnce({ Items: [row('p1')], LastEvaluatedKey: lek })
        .mockResolvedValueOnce({ Items: [row('p2')] });
      (db as any).client = { send: mockSend };

      const page = await db.listPage('motor', 10);
      expect(page.items).toHaveLength(2);
      expect(page.next).toBeUndefined();
      expect(mockSend).toHaveBeenCalledTimes(2);
    });

    it("walks to the following type when type='all' and the first type exhausts", async () => {
      const mockSend = jest
        .fn()
        .mockResolvedValueOnce({ Items: [row('a1')] }) // first type, exhausted
        .mockResolvedValue({ Items: [] }); // every remaining type empty
      (db as any).client = { send: mockSend };

      const page = await db.listPage('all', 10);
      expect(page.items).toHaveLength(1);
      expect(page.next).toBeUndefined();
      // One query per product type in the sequence.
      expect(mockSend.mock.calls.length).toBeGreaterThan(1);
    });

    it("emits a key-less next at an exact type boundary on type='all'", async () => {
      // First type returns exactly `limit` rows with no LastEvaluatedKey:
      // the page is full but the listing isn't done — next points at the
      // following type with no key.
      const mockSend = jest
        .fn()
        .mockResolvedValueOnce({ Items: [row('a1'), row('a2')] });
      (db as any).client = { send: mockSend };

      const page = await db.listPage('all', 2);
      expect(page.items).toHaveLength(2);
      expect(page.next).toBeDefined();
      expect(page.next!.key).toBeUndefined();
      expect(typeof page.next!.type).toBe('string');
      expect(mockSend).toHaveBeenCalledTimes(1);
    });

    it('resumes from a start point', async () => {
      const startKey = { SK: { S: 'PRODUCT#p42' } };
      const mockSend = jest.fn().mockResolvedValue({ Items: [row('p43')] });
      (db as any).client = { send: mockSend };

      // QueryCommand is auto-mocked (no .input on instances), so assert
      // on the constructor call args instead.
      const { QueryCommand } = jest.requireMock('@aws-sdk/client-dynamodb');
      (QueryCommand as jest.Mock).mockClear();

      const page = await db.listPage('motor', 10, { type: 'motor', key: startKey as any });
      expect(page.items).toHaveLength(1);
      const input = (QueryCommand as jest.Mock).mock.calls[0][0];
      expect(input.ExclusiveStartKey).toEqual(startKey);
    });

    it('throws when the start type is not part of the listing', async () => {
      const mockSend = jest.fn();
      (db as any).client = { send: mockSend };

      await expect(
        db.listPage('motor', 10, { type: 'drive' })
      ).rejects.toThrow(/not part of this listing/);
      expect(mockSend).not.toHaveBeenCalled();
    });

    it('propagates DynamoDB errors instead of swallowing them', async () => {
      // Contrast with list(), which returns [] on error: a paginated
      // caller must be able to tell "empty page" from "failed page".
      const mockSend = jest.fn().mockRejectedValue(new Error('throttled'));
      (db as any).client = { send: mockSend };

      await expect(db.listPage('motor', 10)).rejects.toThrow('throttled');
    });
  });
});
