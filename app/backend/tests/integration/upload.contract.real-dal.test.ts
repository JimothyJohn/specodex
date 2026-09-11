/**
 * Real-DAL contract tests for POST /api/upload — the abuse-input half.
 *
 * Sibling to the mocked `tests/upload.contract.test.ts`, which feeds the
 * endpoint hostile bodies (path traversal, null bytes, CRLF, 2 000-char
 * filenames, type-coercion surprises) and asserts the HTTP status alone
 * with `jest.mock('../src/db/dynamodb')`. Because `db.create()` is a
 * stub that unconditionally resolves `true`, every one of those cases
 * reports a clean 201 no matter what the real write path would do with
 * the payload — the mock cannot see `serializeItem`'s
 * `ds.product_type.toUpperCase()`, cannot see marshalling, and cannot
 * see what actually lands in the table.
 *
 * This file closes that gap. Same hostile inputs, driven through the
 * real `DynamoDBService` against DynamoDB Local, asserting on the
 * *stored row*: whether it exists at all, which partition it landed in,
 * and whether the attacker's bytes survived the round-trip verbatim.
 *
 * Distinct from `upload.real-dal.test.ts`, which covers the happy path
 * (PK/SK shape, `pages` list round-trip, `/api/datasheets` listing).
 * What's here and only here:
 *   - traversal / null-byte / CRLF / 2 000-char filenames as *stored* bytes
 *   - the attacker-controlled partition key (`product_type` is
 *     concatenated into PK with no allowlist)
 *   - **the one real discrepancy the mock hides**: a non-string
 *     `product_type` is a 500, not the "< 500" the mocked sibling pins
 *   - **no-leak on rejection**: a 400 must leave the table empty
 *   - **uniqueness at the storage layer**: two identical POSTs are two
 *     rows, not one overwrite
 *
 * This is HARDENING Phase 2.2.b — one more entry off the "remaining
 * mocked backend tests" migration follow-up. Error-injection cases
 * (`db.create()` throws / returns false) stay in the mocked sibling;
 * simulating transport failure is what mocks are for.
 *
 * Same env-redirect trick as the other real-DAL suites: env vars are set
 * at module-load time before `../../src/index` is lazily required, so the
 * upload route module constructs its own `DynamoDBService` against
 * DynamoDB Local without any production-code change.
 */

import type { Application } from 'express';
import request from 'supertest';
import {
  DynamoDBClient,
  DeleteItemCommand,
  ScanCommand,
} from '@aws-sdk/client-dynamodb';
import { unmarshall } from '@aws-sdk/util-dynamodb';

const TABLE_NAME = 'specodex-test';
const ENDPOINT = process.env.MOCK_DYNAMODB_ENDPOINT ?? 'http://localhost:8000';

process.env.DYNAMODB_TABLE_NAME = TABLE_NAME;
process.env.AWS_ENDPOINT_URL_DYNAMODB = ENDPOINT;
process.env.AWS_REGION = 'us-east-1';
process.env.AWS_ACCESS_KEY_ID = 'local';
process.env.AWS_SECRET_ACCESS_KEY = 'local';

// S3 presigning is stubbed for the same reason as the happy-path suite:
// there is no S3 Local in the jest-dynamodb setup, and the DAL write
// happens BEFORE the presign call, so the stub can never mask a
// real-DAL bug.
jest.mock('@aws-sdk/s3-request-presigner', () => ({
  getSignedUrl: jest
    .fn()
    .mockResolvedValue('https://s3.example.com/mock-presigned'),
}));

const baseBody = {
  product_name: 'Test Motor',
  manufacturer: 'TestCorp',
  product_type: 'motor',
  filename: 'datasheet.pdf',
};

function readClient(): DynamoDBClient {
  return new DynamoDBClient({
    region: 'us-east-1',
    endpoint: ENDPOINT,
    credentials: { accessKeyId: 'local', secretAccessKey: 'local' },
  });
}

async function truncateTable(): Promise<void> {
  const client = readClient();
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

async function scanAllRows(): Promise<Array<Record<string, unknown>>> {
  const client = readClient();
  const scan = await client.send(new ScanCommand({ TableName: TABLE_NAME }));
  return (scan.Items ?? []).map((i) => unmarshall(i));
}

/** The single row the table is expected to hold; fails loudly otherwise. */
async function onlyRow(): Promise<Record<string, unknown>> {
  const rows = await scanAllRows();
  expect(rows).toHaveLength(1);
  return rows[0];
}

describe('POST /api/upload — real-DAL abuse-input contract', () => {
  let app: Application;

  beforeAll(() => {
    // Lazy require so the env overrides above are in place before the
    // upload route module constructs its DynamoDBService at import time.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    app = require('../../src/index').default as Application;
  });

  beforeEach(async () => {
    await truncateTable();
  });

  // ------------------------------------------------------------------
  // Hostile filenames — what actually gets persisted
  // ------------------------------------------------------------------

  describe('hostile filenames', () => {
    it('path traversal survives into the stored url but cannot escape the partition', async () => {
      const filename = '../../../etc/passwd.pdf';
      const res = await request(app)
        .post('/api/upload')
        .send({ ...baseBody, filename });
      expect(res.status).toBe(201);

      const row = await onlyRow();
      // The traversal is stored verbatim — the route does not sanitise
      // the filename (the mocked sibling documents the same for the
      // response body; this pins it at rest).
      expect(row.url).toContain('../');
      expect((row.url as string).endsWith(`/${filename}`)).toBe(true);
      // What the mocked sibling structurally cannot check: the traversal
      // is confined to the S3 key. The DynamoDB partition key is derived
      // from product_type only, so `../` never reaches PK/SK.
      expect(row.PK).toBe('DATASHEET#MOTOR');
      expect(row.SK).toBe(`DATASHEET#${res.body.data.datasheet_id}`);
    });

    it('a null byte in the filename round-trips through DynamoDB intact', async () => {
      const filename = 'bad\x00name.pdf';
      const res = await request(app)
        .post('/api/upload')
        .send({ ...baseBody, filename });
      expect(res.status).toBe(201);

      const row = await onlyRow();
      // DynamoDB string attributes are UTF-8 and accept U+0000, so the
      // write succeeds and the byte comes back unchanged. Pinning this
      // matters: if a future marshalling change (or a switch to a store
      // that rejects NUL) made `create()` throw, the route would answer
      // 500 and the mocked sibling's "< 500" assertion would stay green.
      expect((row.url as string).includes('\x00')).toBe(true);
    });

    it('CRLF in the filename is stored raw — the log barrier is log-only', async () => {
      const res = await request(app)
        .post('/api/upload')
        .send({ ...baseBody, filename: 'weird\r\nname.pdf' });
      expect(res.status).toBe(201);

      const row = await onlyRow();
      // `upload.ts` strips CR/LF before `console.log` (the CodeQL
      // log-injection barrier). That strip is deliberately NOT applied to
      // the persisted value — assert the stored bytes still carry the
      // CRLF so a future "sanitise at the source" change is a visible
      // decision rather than a silent one.
      expect(row.url).toContain('\r\n');
    });

    it('a 2 000-character filename persists without hitting an item-size or key limit', async () => {
      const filename = `${'a'.repeat(2000)}.pdf`;
      const res = await request(app)
        .post('/api/upload')
        .send({ ...baseBody, filename });
      expect(res.status).toBe(201);

      const row = await onlyRow();
      expect((row.url as string).endsWith(`/${filename}`)).toBe(true);
      // The 1 024-byte sort-key limit is the thing that would bite here
      // if the filename ever moved into SK. It hasn't: SK is the uuid.
      expect((row.SK as string).length).toBeLessThan(64);
    });

    it('filename of just ".pdf" persists (edge of the endsWith check)', async () => {
      const res = await request(app)
        .post('/api/upload')
        .send({ ...baseBody, filename: '.pdf' });
      expect(res.status).toBe(201);

      const row = await onlyRow();
      expect((row.url as string).endsWith('/.pdf')).toBe(true);
    });
  });

  // ------------------------------------------------------------------
  // Rejections must not leak a row
  // ------------------------------------------------------------------

  describe('validation rejections leave the table empty', () => {
    it.each([
      ['non-PDF extension', 'datasheet.exe'],
      ['double extension', 'spec.PDF.exe'],
      ['near-miss extension', 'file.pdfx'],
      ['truncated extension', 'file.pd'],
      ['no extension', 'noext'],
      ['empty filename', ''],
    ])('%s → 400 and no row is written', async (_name, filename) => {
      const res = await request(app)
        .post('/api/upload')
        .send({ ...baseBody, filename });
      expect(res.status).toBe(400);
      expect(await scanAllRows()).toHaveLength(0);
    });

    it('a non-JSON body is a 4xx and writes nothing', async () => {
      const res = await request(app)
        .post('/api/upload')
        .set('Content-Type', 'text/plain')
        .send('not a json document');
      expect(res.status).toBeGreaterThanOrEqual(400);
      expect(res.status).toBeLessThan(500);
      expect(await scanAllRows()).toHaveLength(0);
    });
  });

  // ------------------------------------------------------------------
  // Type-coercion surprises — where the mock and reality diverge
  // ------------------------------------------------------------------

  describe('type-coercion surprises', () => {
    it('a numeric product_type is a 500 against the real DAL, and writes nothing', async () => {
      const res = await request(app)
        .post('/api/upload')
        .send({ ...baseBody, product_type: 42 });

      // THE DISCREPANCY. The mocked sibling asserts `< 500` here and
      // passes, because its stubbed `create()` never runs
      // `serializeItem`. In reality `ds.product_type.toUpperCase()`
      // throws a TypeError on a number; `DynamoDBService.create()`
      // catches it and returns false; the route maps that to 500.
      //
      // Pinned as-is rather than fixed: the route's validation gap
      // (`!product_type` only checks truthiness, never the type) is a
      // production change, out of scope for a test migration. Filed as a
      // follow-up in todo/HARDENING.md. If that follow-up lands and the
      // endpoint starts answering 400, flip this expectation — the point
      // is that the behaviour is asserted somewhere real.
      expect(res.status).toBe(500);
      expect(res.body.success).toBe(false);
      expect(await scanAllRows()).toHaveLength(0);
    });

    it('an unknown string product_type is accepted and mints its own partition', async () => {
      const res = await request(app)
        .post('/api/upload')
        .send({ ...baseBody, product_type: 'not-a-real-type' });
      expect(res.status).toBe(201);

      const row = await onlyRow();
      // There is no allowlist between the request body and the partition
      // key — `product_type` is uppercased and concatenated. A caller can
      // therefore create arbitrary DATASHEET#* partitions. Not a
      // confidentiality break (the listing Scans `begins_with('DATASHEET#')`,
      // so the row is still visible rather than hidden), but it is
      // attacker-controlled key space and the mocked sibling cannot see it.
      expect(row.PK).toBe('DATASHEET#NOT-A-REAL-TYPE');

      const list = await request(app).get('/api/datasheets');
      expect(list.status).toBe(200);
      expect(list.body.data).toHaveLength(1);
      expect(list.body.data[0].component_type).toBe('not-a-real-type');
    });

    it('a product_name array is marshalled as a list, not stringified', async () => {
      const res = await request(app)
        .post('/api/upload')
        .send({ ...baseBody, product_name: ['a', 'b'] });
      expect(res.status).toBe(201);

      const row = await onlyRow();
      // The DAL performs no coercion: an array in, an array out. The
      // frontend's `Datasheet.product_name: string` contract is therefore
      // only as strong as the route's (absent) type validation.
      expect(Array.isArray(row.product_name)).toBe(true);
      expect(row.product_name).toEqual(['a', 'b']);
    });

    it('a string "pages" is stored as a string, not coerced to a list', async () => {
      const res = await request(app)
        .post('/api/upload')
        .send({ ...baseBody, pages: 'not-an-array' });
      expect(res.status).toBe(201);

      const row = await onlyRow();
      expect(typeof row.pages).toBe('string');
      expect(row.pages).toBe('not-an-array');
    });

    it('a pages array containing null survives — DynamoDB lists are heterogeneous', async () => {
      const res = await request(app)
        .post('/api/upload')
        .send({ ...baseBody, pages: [1, null, 'b'] });
      expect(res.status).toBe(201);

      const row = await onlyRow();
      // `removeUndefinedValues` drops `undefined`, not `null` — a null
      // element marshalls to NULL and comes back as null. `./Quickstart
      // process` reads this field, so pin the shape it will actually see.
      expect(row.pages).toEqual([1, null, 'b']);
    });
  });

  // ------------------------------------------------------------------
  // Uniqueness at the storage layer
  // ------------------------------------------------------------------

  describe('presigned-upload uniqueness', () => {
    it('two identical POSTs write two rows — no key collision, no overwrite', async () => {
      const a = await request(app).post('/api/upload').send(baseBody);
      const b = await request(app).post('/api/upload').send(baseBody);
      expect(a.status).toBe(201);
      expect(b.status).toBe(201);

      // The mocked sibling can only compare the two response bodies.
      // Since PK is derived from product_type alone, a non-unique SK
      // would silently PutItem-overwrite the first row — assert both
      // survive in the table.
      const rows = await scanAllRows();
      expect(rows).toHaveLength(2);
      const sks = rows.map((r) => r.SK).sort();
      expect(sks).toEqual(
        [
          `DATASHEET#${a.body.data.datasheet_id}`,
          `DATASHEET#${b.body.data.datasheet_id}`,
        ].sort(),
      );
    });
  });
});
