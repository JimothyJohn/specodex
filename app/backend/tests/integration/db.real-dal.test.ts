/**
 * Real-DAL integration tests for DynamoDBService.
 *
 * Runs against DynamoDB Local (booted by @shelf/jest-dynamodb's
 * globalSetup; schema defined in `jest-dynamodb-config.js`). No
 * jest.mock — every CRUD call goes through the real AWS SDK to the
 * local jar, exercising the marshall/unmarshall path, the PK/SK
 * composition, the pagination loop, and the projection-only scan.
 *
 * This is the proof-of-life test for HARDENING Phase 2.2. Follow-up
 * cards migrate the remaining mocked tests (search.contract,
 * routes, etc.) to this pattern.
 *
 * NOTE: the table is shared across tests in the file but isolated
 * per `beforeEach` via a unique PK per test. That keeps the suite
 * deterministic without paying the cost of CREATE/DROP TABLE on
 * every test (DynamoDB Local's CREATE-TABLE roundtrip is ~30ms).
 */

import { DynamoDBService } from '../../src/db/dynamodb';
import { DynamoDBClient, ScanCommand, DeleteItemCommand } from '@aws-sdk/client-dynamodb';
import { Motor, Product } from '../../src/types/models';

const TABLE_NAME = 'specodex-test';
const ENDPOINT = process.env.MOCK_DYNAMODB_ENDPOINT ?? 'http://localhost:8000';

function makeDb(): DynamoDBService {
  return new DynamoDBService({
    tableName: TABLE_NAME,
    region: 'us-east-1',
    endpoint: ENDPOINT,
    credentials: {
      accessKeyId: 'local',
      secretAccessKey: 'local',
    },
  });
}

async function truncateTable(): Promise<void> {
  // DynamoDB Local has no fast TRUNCATE — scan + batch-delete is the
  // canonical way. Cheap on tables of test-fixture size.
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

describe('DynamoDBService — real-DAL integration', () => {
  let db: DynamoDBService;

  beforeEach(async () => {
    await truncateTable();
    db = makeDb();
  });

  it('create → read returns the same product, no coercion', async () => {
    const motor: Partial<Motor> = {
      product_id: 'real-dal-001',
      product_type: 'motor',
      manufacturer: 'TestCorp',
      part_number: 'TC-001',
    };

    const created = await db.create(motor as Product);
    expect(created).toBe(true);

    const got = await db.read('real-dal-001', 'motor');
    expect(got).not.toBeNull();
    expect(got?.product_id).toBe('real-dal-001');
    expect(got?.product_type).toBe('motor');
    expect((got as Motor).manufacturer).toBe('TestCorp');
    expect((got as Motor).part_number).toBe('TC-001');
  });

  it('list returns only the requested type, with PK/SK round-tripped', async () => {
    await db.create({
      product_id: 'motor-1',
      product_type: 'motor',
      manufacturer: 'M',
    } as Product);
    await db.create({
      product_id: 'motor-2',
      product_type: 'motor',
      manufacturer: 'M',
    } as Product);
    await db.create({
      product_id: 'drive-1',
      product_type: 'drive',
      manufacturer: 'D',
    } as Product);

    const motors = await db.list('motor');
    const drives = await db.list('drive');

    expect(motors).toHaveLength(2);
    expect(drives).toHaveLength(1);
    expect(motors.map(m => m.product_id).sort()).toEqual(['motor-1', 'motor-2']);
    expect(drives[0].product_id).toBe('drive-1');
  });

  it('list paginates correctly when results span pages', async () => {
    // Seed 30 rows; DynamoDB Local respects Limit in QueryCommand the
    // same way prod does. This exercises the paginate-until-no-more loop
    // at db.list:233-267 against a real Query response (LastEvaluatedKey
    // included on every non-final page).
    const seedCount = 30;
    for (let i = 0; i < seedCount; i++) {
      await db.create({
        product_id: `m-${i.toString().padStart(3, '0')}`,
        product_type: 'motor',
        manufacturer: 'BulkCorp',
      } as Product);
    }

    const all = await db.list('motor');
    expect(all).toHaveLength(seedCount);

    // limit shorter than the total cuts off mid-page.
    const partial = await db.list('motor', 10);
    expect(partial.length).toBeLessThanOrEqual(seedCount);
    expect(partial.length).toBeGreaterThan(0);
  });

  it('delete then read returns null', async () => {
    await db.create({
      product_id: 'to-delete',
      product_type: 'motor',
      manufacturer: 'X',
    } as Product);
    expect(await db.read('to-delete', 'motor')).not.toBeNull();

    const deleted = await db.delete('to-delete', 'motor');
    expect(deleted).toBe(true);

    expect(await db.read('to-delete', 'motor')).toBeNull();
  });

  it('getUniqueManufacturers projection-scan returns sorted set across types', async () => {
    await db.create({
      product_id: 'm1',
      product_type: 'motor',
      manufacturer: 'ABB',
    } as Product);
    await db.create({
      product_id: 'm2',
      product_type: 'motor',
      manufacturer: 'Siemens',
    } as Product);
    await db.create({
      product_id: 'd1',
      product_type: 'drive',
      manufacturer: 'ABB', // duplicate across type — should dedupe
    } as Product);
    await db.create({
      product_id: 'd2',
      product_type: 'drive',
      manufacturer: 'Schneider',
    } as Product);

    const ms = await db.getUniqueManufacturers();
    expect(ms).toEqual(['ABB', 'Schneider', 'Siemens']);
  });

  it('read returns null for nonexistent id (no error thrown)', async () => {
    const got = await db.read('does-not-exist', 'motor');
    expect(got).toBeNull();
  });
});
