/**
 * DynamoDB Local table schema for integration tests.
 *
 * Mirrors the prod table defined in
 * `app/infrastructure/lib/database-stack.ts`: PK (S) partition,
 * SK (S) sort, PAY_PER_REQUEST (mapped to PROVISIONED 5/5 here since
 * DynamoDB Local doesn't simulate billing modes — capacity values
 * are inert for correctness).
 *
 * Loaded by @shelf/jest-dynamodb's globalSetup. The local server
 * binds to port 8000 by default; integration tests connect via
 * `DYNAMODB_ENDPOINT=http://localhost:8000` (see
 * `tests/integration/setup-dynamodb.ts`).
 */
module.exports = {
  tables: [
    {
      TableName: 'specodex-test',
      KeySchema: [
        { AttributeName: 'PK', KeyType: 'HASH' },
        { AttributeName: 'SK', KeyType: 'RANGE' },
      ],
      AttributeDefinitions: [
        { AttributeName: 'PK', AttributeType: 'S' },
        { AttributeName: 'SK', AttributeType: 'S' },
      ],
      ProvisionedThroughput: {
        ReadCapacityUnits: 5,
        WriteCapacityUnits: 5,
      },
    },
  ],
  port: 8000,
  // Don't install jar at config-eval time; @shelf/jest-dynamodb handles
  // the download lazily on first global-setup invocation. Cached in
  // node_modules so CI hits the network once per `npm ci`.
  installerConfig: {
    install_path: 'node_modules/dynamodb-local',
  },
};
