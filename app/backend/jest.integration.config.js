/**
 * Jest config for INTEGRATION tests — real-DAL against DynamoDB Local.
 *
 * Run via `npm run test:integration`. Boots a DynamoDB Local jar in
 * globalSetup (schema from `jest-dynamodb-config.js`), tears down in
 * globalTeardown. First run downloads the jar (~30 MB) into
 * `node_modules/dynamodb-local`; subsequent runs are cached.
 *
 * Excluded from the default `npm test` run because it needs Java
 * on PATH and is materially slower than the unit suite.
 */
module.exports = {
  preset: '@shelf/jest-dynamodb',
  testEnvironment: 'node',
  roots: ['<rootDir>/tests/integration'],
  testMatch: ['**/?(*.)+(spec|test).ts'],
  transform: {
    '^.+\\.ts$': ['ts-jest', { isolatedModules: true }],
  },
  moduleFileExtensions: ['ts', 'js', 'json', 'node'],
  // AWS SDK v3 uses lazy submodule imports under @smithy/* that jest
  // doesn't transform by default — let them through so the SDK
  // initializes correctly when integration tests load it directly.
  transformIgnorePatterns: [
    '/node_modules/(?!(@aws-sdk|@smithy|uuid)/)',
  ],
};
