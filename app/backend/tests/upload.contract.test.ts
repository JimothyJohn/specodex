/**
 * Contract tests for POST /api/upload — the one user-writable endpoint in
 * public mode. Every field in the body is attacker-controlled, so the
 * boundary between request and S3-key generation matters.
 *
 * Complements existing upload.test.ts by focusing on abuse inputs: path
 * traversal, null bytes, extreme lengths, unicode, type coercion surprises.
 */

import request from 'supertest';
import app from '../src/index';
import { DynamoDBService } from '../src/db/dynamodb';

jest.mock('../src/db/dynamodb');
jest.mock('@aws-sdk/s3-request-presigner', () => ({
  getSignedUrl: jest.fn().mockResolvedValue('https://s3.example.com/presigned-url'),
}));

const baseBody = {
  product_name: 'Test Motor',
  manufacturer: 'TestCorp',
  product_type: 'motor',
  filename: 'datasheet.pdf',
};

function ok() {
  (DynamoDBService.prototype.create as jest.Mock).mockResolvedValue(true);
}

describe('POST /api/upload — filename validation', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    ok();
  });

  it.each(['datasheet.exe', 'spec.PDF.exe', 'file.pdfx', 'file.pd', 'noext'])(
    'rejects non-PDF filename "%s" with 400',
    async (filename) => {
      const res = await request(app).post('/api/upload').send({ ...baseBody, filename });
      expect(res.status).toBe(400);
    },
  );

  it('accepts .pdf, .PDF, mixed case', async () => {
    for (const filename of ['a.pdf', 'A.PDF', 'Mixed.Pdf']) {
      const res = await request(app).post('/api/upload').send({ ...baseBody, filename });
      expect(res.status).toBe(201);
    }
  });
});

describe('POST /api/upload — abuse inputs', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    ok();
  });

  it('accepts filename with path traversal literals — but S3 key contains them as-is', async () => {
    // Document current behavior: filename is NOT sanitized. The traversal goes
    // into the S3 key `queue/<id>/${filename}`. S3 treats slashes as prefix
    // delimiters, not fs traversal — no file escape is possible. But we still
    // don't want `../` ending up in audit logs cleanly, so this test flags it.
    const res = await request(app)
      .post('/api/upload')
      .send({ ...baseBody, filename: '../../../etc/passwd.pdf' });
    expect(res.status).toBe(201);
    const key = res.body.data.s3_key as string;
    // Current behavior: traversal present. If someone later sanitizes, flip this.
    expect(key).toContain('../');
  });

  it('filename with null byte does not produce a 500', async () => {
    const res = await request(app)
      .post('/api/upload')
      .send({ ...baseBody, filename: 'bad\x00name.pdf' });
    expect(res.status).toBeLessThan(500);
  });

  it('filename with CRLF is handled deterministically', async () => {
    const res = await request(app)
      .post('/api/upload')
      .send({ ...baseBody, filename: 'weird\r\nname.pdf' });
    expect(res.status).toBeLessThan(500);
  });

  it('extremely long filename is either accepted or 400, not 500', async () => {
    const filename = 'a'.repeat(2000) + '.pdf';
    const res = await request(app).post('/api/upload').send({ ...baseBody, filename });
    expect(res.status).toBeLessThan(500);
  });

  it('filename of just an extension ".pdf" passes (edge of endsWith)', async () => {
    const res = await request(app).post('/api/upload').send({ ...baseBody, filename: '.pdf' });
    expect(res.status).toBe(201);
  });

  it('empty-string filename is 400', async () => {
    const res = await request(app).post('/api/upload').send({ ...baseBody, filename: '' });
    expect(res.status).toBe(400);
  });
});

describe('POST /api/upload — type coercion surprises', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    ok();
  });

  it('product_type as number is a 400 (type guard), never a 500', async () => {
    const res = await request(app).post('/api/upload').send({ ...baseBody, product_type: 42 });
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/product_type/);
  });

  it('product_name as array is a 400 (type guard), never a 500', async () => {
    const res = await request(app)
      .post('/api/upload')
      .send({ ...baseBody, product_name: ['a', 'b'] });
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/product_name/);
  });

  it('filename as number is a 400, not a TypeError on toLowerCase', async () => {
    const res = await request(app).post('/api/upload').send({ ...baseBody, filename: 7 });
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/filename/);
  });

  it('pages as non-array (string) is passed through without crash', async () => {
    const res = await request(app)
      .post('/api/upload')
      .send({ ...baseBody, pages: 'not-an-array' });
    expect(res.status).toBeLessThan(500);
  });

  it('pages as array of bogus strings is passed through without crash', async () => {
    const res = await request(app)
      .post('/api/upload')
      .send({ ...baseBody, pages: ['a', 'b', null] });
    expect(res.status).toBeLessThan(500);
  });
});

describe('POST /api/upload — presigned URL uniqueness', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    ok();
  });

  it('repeated identical calls each get a new datasheet_id', async () => {
    const resA = await request(app).post('/api/upload').send(baseBody);
    const resB = await request(app).post('/api/upload').send(baseBody);
    expect(resA.body.data.datasheet_id).not.toBe(resB.body.data.datasheet_id);
    expect(resA.body.data.s3_key).not.toBe(resB.body.data.s3_key);
  });
});

describe('POST /api/upload — content-type handling', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    ok();
  });

  it('rejects non-JSON body with a 4xx (not a 500)', async () => {
    const res = await request(app)
      .post('/api/upload')
      .set('Content-Type', 'text/plain')
      .send('not a json document');
    expect(res.status).toBeGreaterThanOrEqual(400);
    expect(res.status).toBeLessThan(500);
  });
});
