/**
 * Real-DAL integration tests for admin operations (diff / promote /
 * demote / purge) — HARDENING Phase 2.2 step 5.
 *
 * The mocked sibling (`tests/adminOperations.test.ts`) swaps
 * `DynamoDBService` for a hand-rolled fake whose `batchCreate` pushes
 * items onto an array and returns `items.length`, and whose
 * `batchDelete` records keys. That shape can only assert "the operation
 * asked the DAL to do X" — it takes on faith that asking produces the
 * row movement the operator expects. Everything below asserts on the
 * *tables* instead:
 *
 *  - a dry run leaves the target table empty, rather than merely
 *    skipping a mock call;
 *  - `batchCreate` past DynamoDB's 25-item BatchWriteItem cap actually
 *    lands every row (the fake's single array push cannot see a
 *    chunking bug);
 *  - `purge` hand-builds its keys as `PRODUCT#${type.toUpperCase()}` /
 *    `PRODUCT#${product_id}` rather than reading them off the row, so
 *    the fake reports a delete for keys that would match nothing. Here
 *    the rows have to be gone;
 *  - a blacklisted manufacturer leaves *no* row in the target, not just
 *    a smaller count in the result envelope.
 *
 * Two tables are needed: promote/demote/diff move rows between stage
 * tables, and a single table can't distinguish "wrote to target" from
 * "read from source". `specodex-test-target` is declared alongside
 * `specodex-test` in `jest-dynamodb-config.js`.
 *
 * Error-injection cases (DAL throws → operation surfaces the failure)
 * stay in the mocked sibling, which is the right tool for them.
 */

import fs from 'fs';
import os from 'os';
import path from 'path';

import {
  DynamoDBClient,
  ScanCommand,
  DeleteItemCommand,
} from '@aws-sdk/client-dynamodb';

import { DynamoDBService } from '../../src/db/dynamodb';
import { Drive, Product } from '../../src/types/models';
import { Blacklist } from '../../src/services/blacklist';
import {
  demote,
  diff,
  promote,
  purge,
} from '../../src/services/adminOperations';

const SOURCE_TABLE = 'specodex-test';
const TARGET_TABLE = 'specodex-test-target';
const ENDPOINT = process.env.MOCK_DYNAMODB_ENDPOINT ?? 'http://localhost:8000';
const CREDENTIALS = { accessKeyId: 'local', secretAccessKey: 'local' };

function makeDb(tableName: string): DynamoDBService {
  return new DynamoDBService({
    tableName,
    region: 'us-east-1',
    endpoint: ENDPOINT,
    credentials: CREDENTIALS,
  });
}

async function truncate(tableName: string): Promise<void> {
  const client = new DynamoDBClient({
    region: 'us-east-1',
    endpoint: ENDPOINT,
    credentials: CREDENTIALS,
  });
  const scan = await client.send(
    new ScanCommand({ TableName: tableName, ProjectionExpression: 'PK, SK' }),
  );
  for (const item of scan.Items ?? []) {
    await client.send(
      new DeleteItemCommand({
        TableName: tableName,
        Key: { PK: item.PK!, SK: item.SK! },
      }),
    );
  }
}

/** Every row in a table, keys only — the ground truth these tests
 *  assert against, read straight from DynamoDB rather than through the
 *  service that wrote them. */
async function rawKeys(tableName: string): Promise<{ PK: string; SK: string }[]> {
  const client = new DynamoDBClient({
    region: 'us-east-1',
    endpoint: ENDPOINT,
    credentials: CREDENTIALS,
  });
  const scan = await client.send(
    new ScanCommand({ TableName: tableName, ProjectionExpression: 'PK, SK' }),
  );
  return (scan.Items ?? []).map(i => ({ PK: i.PK!.S!, SK: i.SK!.S! }));
}

function makeDrive(manufacturer: string, productId: string): Drive {
  return {
    product_id: productId,
    product_type: 'drive',
    product_name: `${manufacturer}-drive`,
    manufacturer,
  } as Drive;
}

function tmpBlacklist(entries: string[] = []): Blacklist {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bl-real-dal-'));
  const file = path.join(dir, 'blacklist.json');
  fs.writeFileSync(file, JSON.stringify({ banned_manufacturers: entries }));
  return new Blacklist(file);
}

describe('adminOperations — real-DAL integration', () => {
  let source: DynamoDBService;
  let target: DynamoDBService;

  beforeEach(async () => {
    await truncate(SOURCE_TABLE);
    await truncate(TARGET_TABLE);
    source = makeDb(SOURCE_TABLE);
    target = makeDb(TARGET_TABLE);
  });

  describe('diff', () => {
    it('partitions real rows by product_id across two tables', async () => {
      await source.create(makeDrive('ACME', 'both-1') as Product);
      await source.create(makeDrive('ACME', 'src-only') as Product);
      await target.create(makeDrive('ACME', 'both-1') as Product);
      await target.create(makeDrive('ACME', 'tgt-only') as Product);

      const result = await diff({
        source,
        target,
        productType: 'drive',
        sourceStage: 'dev',
        targetStage: 'prod',
      });

      expect(result.only_in_source).toEqual(['src-only']);
      expect(result.only_in_target).toEqual(['tgt-only']);
      expect(result.in_both_count).toBe(1);
    });

    it('manufacturer filter narrows both sides', async () => {
      await source.create(makeDrive('ACME', 'acme-1') as Product);
      await source.create(makeDrive('Other', 'other-1') as Product);

      const result = await diff({
        source,
        target,
        productType: 'drive',
        sourceStage: 'dev',
        targetStage: 'prod',
        manufacturer: 'ACME',
      });

      expect(result.only_in_source).toEqual(['acme-1']);
    });
  });

  describe('promote', () => {
    it('dry run writes nothing to the target table', async () => {
      await source.create(makeDrive('ACME', 'd-1') as Product);
      await source.create(makeDrive('ACME', 'd-2') as Product);

      const result = await promote({
        source,
        target,
        productType: 'drive',
        blacklist: tmpBlacklist(),
        apply: false,
      });

      // The envelope reports what *would* move...
      expect(result.applied).toBe(false);
      expect(result.promoted_products).toBe(2);
      // ...and the target is still empty. The mocked sibling can only
      // assert the first half.
      expect(await rawKeys(TARGET_TABLE)).toEqual([]);
    });

    it('apply lands every row in the target, source untouched', async () => {
      await source.create(makeDrive('ACME', 'd-1') as Product);
      await source.create(makeDrive('ACME', 'd-2') as Product);

      const result = await promote({
        source,
        target,
        productType: 'drive',
        blacklist: tmpBlacklist(),
        apply: true,
      });

      expect(result.applied).toBe(true);
      expect(result.promoted_products).toBe(2);

      const promoted = await target.list('drive');
      expect(promoted.map(p => p.product_id).sort()).toEqual(['d-1', 'd-2']);
      // Promote copies; it never removes from the source stage.
      expect((await source.list('drive')).map(p => p.product_id).sort()).toEqual([
        'd-1',
        'd-2',
      ]);
    });

    it('lands more rows than one BatchWriteItem call can carry', async () => {
      // DynamoDB caps BatchWriteItem at 25 items; batchCreate chunks.
      // The fake DAL's `items.push(...items)` returns the full length
      // whether or not the chunking is correct, so an off-by-one in the
      // loop bound is invisible to the mocked sibling and silently drops
      // rows in a real promote.
      const ids = Array.from({ length: 30 }, (_, i) => `bulk-${String(i).padStart(2, '0')}`);
      for (const id of ids) {
        await source.create(makeDrive('ACME', id) as Product);
      }

      const result = await promote({
        source,
        target,
        productType: 'drive',
        blacklist: tmpBlacklist(),
        apply: true,
      });

      expect(result.promoted_products).toBe(30);
      const promoted = await target.list('drive');
      expect(promoted).toHaveLength(30);
      expect(promoted.map(p => p.product_id).sort()).toEqual([...ids].sort());
    });

    it('a blacklisted manufacturer leaves no row in the target', async () => {
      await source.create(makeDrive('BadCo', 'bad-1') as Product);
      await source.create(makeDrive('ACME', 'good-1') as Product);

      const result = await promote({
        source,
        target,
        productType: 'drive',
        blacklist: tmpBlacklist(['BadCo']),
        apply: true,
      });

      expect(result.blocked_by_blacklist).toBe(1);
      expect(result.blocked_manufacturers).toEqual(['BadCo']);

      const promoted = await target.list('drive');
      expect(promoted.map(p => p.product_id)).toEqual(['good-1']);
      // The point of the blacklist: the banned vendor's row is absent
      // from the target partition entirely, not merely uncounted.
      const badKeys = (await rawKeys(TARGET_TABLE)).filter(k =>
        k.SK.includes('bad-1'),
      );
      expect(badKeys).toEqual([]);
    });

    it('blacklist matching is case-insensitive against stored rows', async () => {
      await source.create(makeDrive('BadCo', 'bad-1') as Product);

      const result = await promote({
        source,
        target,
        productType: 'drive',
        blacklist: tmpBlacklist(['badco']),
        apply: true,
      });

      expect(result.promoted_products).toBe(0);
      expect(await rawKeys(TARGET_TABLE)).toEqual([]);
    });
  });

  describe('demote', () => {
    it('copies back with no blacklist check', async () => {
      // Demote is the rollback path — it deliberately moves everything,
      // banned vendors included. Asserting that against real tables is
      // the only way to be sure no filter crept in.
      await source.create(makeDrive('BadCo', 'bad-1') as Product);
      await source.create(makeDrive('ACME', 'good-1') as Product);

      const result = await demote({
        source,
        target,
        productType: 'drive',
        apply: true,
      });

      expect(result.blocked_by_blacklist).toBe(0);
      const demoted = await target.list('drive');
      expect(demoted.map(p => p.product_id).sort()).toEqual(['bad-1', 'good-1']);
    });

    it('dry run writes nothing', async () => {
      await source.create(makeDrive('ACME', 'd-1') as Product);

      const result = await demote({
        source,
        target,
        productType: 'drive',
        apply: false,
      });

      expect(result.promoted_products).toBe(1);
      expect(await rawKeys(TARGET_TABLE)).toEqual([]);
    });
  });

  describe('purge', () => {
    it('dry run deletes nothing', async () => {
      await source.create(makeDrive('ACME', 'd-1') as Product);

      const result = await purge({
        db: source,
        stage: 'dev',
        productType: 'drive',
        apply: false,
      });

      expect(result.matched).toBe(1);
      expect(result.deleted).toBe(0);
      expect(await source.list('drive')).toHaveLength(1);
    });

    it('apply removes the matched rows from the table', async () => {
      // purge hand-builds `PK: PRODUCT#${type.toUpperCase()}` and
      // `SK: PRODUCT#${product_id}` rather than reading the keys off the
      // row it just listed. If that composition ever drifts from what
      // create() writes, the mocked sibling still records a "delete" for
      // keys matching nothing — DynamoDB's DeleteItem is a happy no-op
      // on an absent key. Only a real table notices the rows survived.
      await source.create(makeDrive('ACME', 'd-1') as Product);
      await source.create(makeDrive('ACME', 'd-2') as Product);

      const result = await purge({
        db: source,
        stage: 'dev',
        productType: 'drive',
        apply: true,
      });

      expect(result.matched).toBe(2);
      expect(result.deleted).toBe(2);
      expect(await source.list('drive')).toEqual([]);
      expect(await rawKeys(SOURCE_TABLE)).toEqual([]);
    });

    it('manufacturer filter spares the other vendor rows', async () => {
      await source.create(makeDrive('ACME', 'acme-1') as Product);
      await source.create(makeDrive('Other', 'other-1') as Product);

      const result = await purge({
        db: source,
        stage: 'dev',
        productType: 'drive',
        manufacturer: 'ACME',
        apply: true,
      });

      expect(result.deleted).toBe(1);
      const left = await source.list('drive');
      expect(left.map(p => p.product_id)).toEqual(['other-1']);
    });

    it('type filter spares the other product types', async () => {
      await source.create(makeDrive('ACME', 'drive-1') as Product);
      await source.create({
        product_id: 'motor-1',
        product_type: 'motor',
        manufacturer: 'ACME',
      } as Product);

      await purge({
        db: source,
        stage: 'dev',
        productType: 'drive',
        apply: true,
      });

      expect(await source.list('drive')).toEqual([]);
      expect((await source.list('motor')).map(p => p.product_id)).toEqual(['motor-1']);
    });

    it('deletes more rows than one BatchWriteItem call can carry', async () => {
      // Same 25-item cap as promote, on the delete side.
      const ids = Array.from({ length: 30 }, (_, i) => `purge-${String(i).padStart(2, '0')}`);
      for (const id of ids) {
        await source.create(makeDrive('ACME', id) as Product);
      }

      const result = await purge({
        db: source,
        stage: 'dev',
        productType: 'drive',
        apply: true,
      });

      expect(result.matched).toBe(30);
      expect(result.deleted).toBe(30);
      expect(await source.list('drive')).toEqual([]);
    });

    it('manufacturer-only purge sweeps every promotable type', async () => {
      await source.create(makeDrive('ACME', 'drive-1') as Product);
      await source.create({
        product_id: 'motor-1',
        product_type: 'motor',
        manufacturer: 'ACME',
      } as Product);
      await source.create({
        product_id: 'gear-1',
        product_type: 'gearhead',
        manufacturer: 'ACME',
      } as Product);

      const result = await purge({
        db: source,
        stage: 'dev',
        manufacturer: 'ACME',
        apply: true,
      });

      expect(result.matched).toBe(3);
      expect(await rawKeys(SOURCE_TABLE)).toEqual([]);
    });

    it('refuses an unfiltered purge before touching the table', async () => {
      await source.create(makeDrive('ACME', 'd-1') as Product);

      await expect(
        purge({ db: source, stage: 'dev', apply: true }),
      ).rejects.toThrow(/at least one/);

      // The guard is the only thing between an operator typo and an
      // empty prod table — assert the rows are still there.
      expect(await source.list('drive')).toHaveLength(1);
    });
  });
});
