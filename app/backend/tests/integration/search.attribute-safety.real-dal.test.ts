/**
 * Real-DAL sibling to `tests/search.attribute-safety.test.ts`.
 *
 * The mocked test pins prototype-pollution / reserved-property safety on
 * GET /api/v1/search by stubbing `DynamoDBService.prototype.list`. That
 * mock means a refactor that broke the real marshall/unmarshall round-trip
 * of a Product (e.g. one that accidentally handed the route a
 * null-prototype object or coerced field names) would still pass the
 * attribute-safety assertions green — the reserved-word lookup would just
 * hit the wrong shape.
 *
 * This file closes that gap: it seeds real Product rows into DynamoDB
 * Local and drives the same adversarial `where=` / `sort=` inputs through
 * the live route end-to-end. `getProductField` (`src/services/search.ts`)
 * runs against DB-round-tripped Products, so any change to marshall
 * semantics that would break the reserved-word defense shows up here.
 *
 * HARDENING Phase 2.2.b — one more mocked backend test migrated to the
 * real DAL. Follows the setup pattern in
 * `tests/integration/search.real-dal.test.ts`: env overrides set BEFORE
 * the app module is required so the route's internally-constructed
 * DynamoDBService points at DynamoDB Local.
 */

import type { Application } from 'express';
import request from 'supertest';
import { DynamoDBService } from '../../src/db/dynamodb';
import {
  DynamoDBClient,
  ScanCommand,
  DeleteItemCommand,
} from '@aws-sdk/client-dynamodb';
import { Product } from '../../src/types/models';

const TABLE_NAME = 'specodex-test';
const ENDPOINT = process.env.MOCK_DYNAMODB_ENDPOINT ?? 'http://localhost:8000';

process.env.DYNAMODB_TABLE_NAME = TABLE_NAME;
process.env.AWS_ENDPOINT_URL_DYNAMODB = ENDPOINT;
process.env.AWS_REGION = 'us-east-1';
process.env.AWS_ACCESS_KEY_ID = 'local';
process.env.AWS_SECRET_ACCESS_KEY = 'local';

function seedDb(): DynamoDBService {
  return new DynamoDBService({
    tableName: TABLE_NAME,
    region: 'us-east-1',
    endpoint: ENDPOINT,
    credentials: { accessKeyId: 'local', secretAccessKey: 'local' },
  });
}

async function truncateTable(): Promise<void> {
  const client = new DynamoDBClient({
    region: 'us-east-1',
    endpoint: ENDPOINT,
    credentials: { accessKeyId: 'local', secretAccessKey: 'local' },
  });
  const scan = await client.send(
    new ScanCommand({ TableName: TABLE_NAME, ProjectionExpression: 'PK, SK' }),
  );
  for (const item of scan.Items ?? []) {
    await client.send(
      new DeleteItemCommand({
        TableName: TABLE_NAME,
        Key: { PK: item.PK!, SK: item.SK! },
      }),
    );
  }
}

describe('GET /api/v1/search — attribute safety (real-DAL)', () => {
  let app: Application;
  let db: DynamoDBService;

  beforeAll(() => {
    app = require('../../src/index').default as Application;
  });

  beforeEach(async () => {
    await truncateTable();
    db = seedDb();
    await db.create({
      product_id: 'a',
      product_type: 'motor',
      manufacturer: 'Acme',
      product_name: 'X',
      rated_power: { value: 100, unit: 'W' },
    } as unknown as Product);
    await db.create({
      product_id: 'b',
      product_type: 'motor',
      manufacturer: 'Beta',
      product_name: 'Y',
      rated_power: { value: 50, unit: 'W' },
    } as unknown as Product);
  });

  describe('where= reserved property names', () => {
    it.each([
      '__proto__>0',
      'constructor>0',
      'prototype>0',
      'toString>0',
      'hasOwnProperty>0',
      'valueOf>0',
    ])('where=%s does not 500 and does not match products', async (expr) => {
      const res = await request(app)
        .get('/api/v1/search')
        .query({ type: 'motor', where: expr });
      expect(res.status).toBeLessThan(500);
      if (res.status === 200) {
        expect(res.body.count).toBe(0);
      }
    });

    it('where=__proto__.polluted=1 does not mutate Object.prototype', async () => {
      await request(app)
        .get('/api/v1/search')
        .query({ type: 'motor', where: '__proto__.polluted=1' });
      expect(({} as Record<string, unknown>).polluted).toBeUndefined();
    });
  });

  describe('sort= reserved property names', () => {
    it.each(['__proto__', 'constructor', 'prototype:desc', 'toString:asc'])(
      'sort=%s does not 500',
      async (expr) => {
        const res = await request(app)
          .get('/api/v1/search')
          .query({ type: 'motor', sort: expr });
        expect(res.status).toBeLessThan(500);
      },
    );
  });

  describe('dotted path attributes', () => {
    it.each([
      'rated_power.value>50',
      'rated_power.unit=W',
      'rated_voltage.min>=10',
    ])('where=%s is either ignored or handled — never 500', async (expr) => {
      const res = await request(app)
        .get('/api/v1/search')
        .query({ type: 'motor', where: expr });
      expect(res.status).toBeLessThan(500);
    });
  });

  describe('response does not echo attacker-controlled field name', () => {
    it('unknown field name is not reflected unescaped in error body', async () => {
      const xss = '<img src=x onerror=1>';
      const res = await request(app)
        .get('/api/v1/search')
        .query({ type: 'motor', where: `${xss}>0` });
      expect(res.status).toBeLessThan(500);
      const body = JSON.stringify(res.body);
      expect(body).not.toMatch(/<img[^>]*onerror/);
    });
  });

  describe('DB-round-tripped products still expose only own properties', () => {
    // The mocked test cannot verify this: its `mockProducts` are plain
    // object literals, whereas real DAL rows are the output of
    // AWS SDK unmarshall. This test pins that the unmarshalled objects
    // still carry a normal prototype chain (so reserved-word lookups hit
    // the prototype and correctly evaluate to `undefined`, not to a
    // stray own-property named `__proto__` etc.).
    it('list() returns products whose own-property names do not include reserved words', async () => {
      const products = await db.list();
      expect(products.length).toBeGreaterThan(0);
      const reserved = ['__proto__', 'constructor', 'prototype', 'toString', 'hasOwnProperty', 'valueOf'];
      for (const p of products) {
        for (const name of reserved) {
          expect(Object.prototype.hasOwnProperty.call(p, name)).toBe(false);
        }
      }
    });
  });
});
