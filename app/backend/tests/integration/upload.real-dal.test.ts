/**
 * Real-DAL integration tests for POST /api/upload.
 *
 * Sibling to the mocked `tests/upload.test.ts`, which pins the HTTP
 * contract (400s for missing fields / wrong extension, 201 with
 * presigned URL on success) with `jest.mock('../src/db/dynamodb')`.
 * The mocks mean a refactor that breaks the real DAL write path —
 * `serializeItem`'s Datasheet-branch detection (`'url' in item && !('product_id' in item)`),
 * the PK/SK composition (`DATASHEET#{TYPE}` + `DATASHEET#{uuid}`),
 * marshall/unmarshall of the queued-datasheet shape (`url`, `status`,
 * `uploaded_at`, optional `pages`) — passes green in the mocked
 * sibling. This file closes that gap by letting `db.create()` fire
 * against DynamoDB Local and confirming the row lands with the
 * correct key structure, then round-trips out through
 * `GET /api/datasheets`.
 *
 * S3 presigning stays mocked — signing a URL is client-side crypto
 * against SDK config, orthogonal to the DAL. What matters here is
 * that the datasheet queue row actually persists so `./Quickstart
 * process` can pick it up.
 *
 * This is HARDENING Phase 2.2.b — one more entry off the "remaining
 * 13 mocked backend tests" migration follow-up. Error-injection
 * cases (`db.create()` throws / returns false) stay in the mocked
 * sibling; simulating transport failure is what mocks are for.
 *
 * Same env-redirect trick as the sibling real-DAL suites: env vars
 * are set at module-load time before `../../src/index` is lazily
 * required, so the upload route module constructs its own
 * `DynamoDBService` against DynamoDB Local without any
 * production-code change.
 */

import type { Application } from 'express';
import request from 'supertest';
import { DynamoDBService } from '../../src/db/dynamodb';
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

// The upload route calls `getSignedUrl` against real S3 config; there
// is no S3 Local equivalent in the jest-dynamodb setup. Stub it — the
// signature/URL is exercised by the AWS SDK's own tests, and mocking
// keeps this suite hermetic. The DAL write happens BEFORE the presign
// call, so this mock never masks a real-DAL bug.
jest.mock('@aws-sdk/s3-request-presigner', () => ({
  getSignedUrl: jest
    .fn()
    .mockResolvedValue('https://s3.example.com/mock-presigned'),
}));

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

describe('POST /api/upload — real-DAL integration', () => {
  let app: Application;

  beforeAll(() => {
    // Lazy require so the env overrides above are in place before the
    // upload route module constructs its DynamoDBService at import time.
    app = require('../../src/index').default as Application;
    // Reference to keep the DAL-friendly type visible; `readClient()`
    // is what actually reads the queue rows out.
    void DynamoDBService;
  });

  beforeEach(async () => {
    await truncateTable();
  });

  describe('happy path — datasheet row lands in DynamoDB', () => {
    it('POST persists a queued datasheet row with the expected PK/SK/status shape', async () => {
      const res = await request(app).post('/api/upload').send({
        product_name: 'Real DAL Motor',
        manufacturer: 'ACME',
        product_type: 'motor',
        filename: 'datasheet.pdf',
      });

      expect(res.status).toBe(201);
      expect(res.body.success).toBe(true);
      expect(typeof res.body.data.datasheet_id).toBe('string');
      expect(res.body.data.datasheet_id).toMatch(/^[0-9a-f-]{36}$/);
      expect(res.body.data.upload_url).toBe(
        'https://s3.example.com/mock-presigned',
      );
      const s3Key: string = res.body.data.s3_key;
      expect(s3Key.startsWith(`queue/${res.body.data.datasheet_id}/`)).toBe(
        true,
      );
      expect(s3Key.endsWith('datasheet.pdf')).toBe(true);

      const rows = await scanAllRows();
      expect(rows).toHaveLength(1);
      const row = rows[0];
      expect(row.PK).toBe('DATASHEET#MOTOR');
      expect(row.SK).toBe(`DATASHEET#${res.body.data.datasheet_id}`);
      expect(row.datasheet_id).toBe(res.body.data.datasheet_id);
      expect(row.product_type).toBe('motor');
      expect(row.product_name).toBe('Real DAL Motor');
      expect(row.manufacturer).toBe('ACME');
      expect(row.status).toBe('queued');
      expect(typeof row.uploaded_at).toBe('string');
      expect(() => new Date(row.uploaded_at as string).toISOString()).not.toThrow();
      expect(typeof row.url).toBe('string');
      expect((row.url as string).startsWith('s3://')).toBe(true);
      expect((row.url as string).endsWith(`/${s3Key}`)).toBe(true);
    });

    it('uppercase .PDF filename still persists a real row (case-insensitive extension check)', async () => {
      const res = await request(app).post('/api/upload').send({
        product_name: 'Upper Ext',
        manufacturer: 'BetaCo',
        product_type: 'drive',
        filename: 'DATASHEET.PDF',
      });
      expect(res.status).toBe(201);

      const rows = await scanAllRows();
      expect(rows).toHaveLength(1);
      expect(rows[0].PK).toBe('DATASHEET#DRIVE');
      // The s3Key preserves the original filename casing — the extension
      // check is case-insensitive but the storage path is not.
      expect((rows[0].url as string).endsWith('DATASHEET.PDF')).toBe(true);
    });

    it('pages array survives the DAL round-trip', async () => {
      const res = await request(app).post('/api/upload').send({
        product_name: 'With Pages',
        manufacturer: 'Gamma',
        product_type: 'gearhead',
        filename: 'pages.pdf',
        pages: [1, 2, 3, 7],
      });
      expect(res.status).toBe(201);

      const rows = await scanAllRows();
      expect(rows).toHaveLength(1);
      // DynamoDB marshalls JS arrays of numbers to `L` (list), which
      // unmarshalls back to a JS array — pin that shape here so a
      // future refactor to number-set (`NS`) is caught, which would
      // silently reorder + dedupe.
      expect(Array.isArray(rows[0].pages)).toBe(true);
      expect(rows[0].pages).toEqual([1, 2, 3, 7]);
    });
  });

  describe('validation rejections — no row leaks into DynamoDB', () => {
    it.each([
      ['missing product_name', { manufacturer: 'X', product_type: 'motor', filename: 'a.pdf' }],
      ['missing manufacturer', { product_name: 'X', product_type: 'motor', filename: 'a.pdf' }],
      ['missing product_type', { product_name: 'X', manufacturer: 'X', filename: 'a.pdf' }],
      ['missing filename', { product_name: 'X', manufacturer: 'X', product_type: 'motor' }],
      ['non-PDF extension', {
        product_name: 'X',
        manufacturer: 'X',
        product_type: 'motor',
        filename: 'datasheet.docx',
      }],
    ])('%s → 400 and DynamoDB stays empty', async (_name, body) => {
      const res = await request(app).post('/api/upload').send(body);
      expect(res.status).toBe(400);
      const rows = await scanAllRows();
      expect(rows).toHaveLength(0);
    });
  });

  describe('/api/datasheets round-trip — queued row surfaces on the listing', () => {
    it('POST /api/upload then GET /api/datasheets returns the queued row with the datasheet mapping', async () => {
      const created = await request(app).post('/api/upload').send({
        product_name: 'Round-trip Motor',
        manufacturer: 'RoundCorp',
        product_type: 'motor',
        filename: 'rt.pdf',
      });
      expect(created.status).toBe(201);
      const datasheetId: string = created.body.data.datasheet_id;

      const list = await request(app).get('/api/datasheets');
      expect(list.status).toBe(200);
      expect(list.body.data).toHaveLength(1);
      const row = list.body.data[0];
      // The listDatasheets → route mapper collapses the stored per-type
      // `product_type` into the discriminator `'datasheet'` and moves
      // the original type onto `component_type`. Pin that.
      expect(row.product_type).toBe('datasheet');
      expect(row.component_type).toBe('motor');
      expect(row.datasheet_id).toBe(datasheetId);
      expect(row.status).toBe('queued');
      expect(row.manufacturer).toBe('RoundCorp');
      expect(row.product_name).toBe('Round-trip Motor');
    });

    it('two uploads of different component types land in distinct DATASHEET partitions', async () => {
      const motor = await request(app).post('/api/upload').send({
        product_name: 'MP-1',
        manufacturer: 'MP',
        product_type: 'motor',
        filename: 'm.pdf',
      });
      const drive = await request(app).post('/api/upload').send({
        product_name: 'DP-1',
        manufacturer: 'DP',
        product_type: 'drive',
        filename: 'd.pdf',
      });
      expect(motor.status).toBe(201);
      expect(drive.status).toBe(201);

      const rows = await scanAllRows();
      const pks = rows.map((r) => r.PK).sort();
      expect(pks).toEqual(['DATASHEET#DRIVE', 'DATASHEET#MOTOR']);

      const list = await request(app).get('/api/datasheets');
      expect(list.body.data).toHaveLength(2);
      const componentTypes = list.body.data.map(
        (d: { component_type: string }) => d.component_type,
      ).sort();
      expect(componentTypes).toEqual(['drive', 'motor']);
    });
  });
});
