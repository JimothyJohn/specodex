/**
 * Jest config for UNIT tests — the existing mocked-DAL suite.
 *
 * Fast feedback (≤5s). Pure jest.mock; no infra. Runs by default
 * via `npm test`. Tests under `tests/integration/` are excluded —
 * they run via `npm run test:integration` against DynamoDB Local
 * (see `jest.integration.config.js`).
 */
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/src', '<rootDir>/tests'],
  testMatch: ['**/__tests__/**/*.ts', '**/?(*.)+(spec|test).ts'],
  testPathIgnorePatterns: ['/node_modules/', '/tests/integration/'],
  collectCoverageFrom: [
    'src/**/*.ts',
    '!src/**/*.d.ts',
    '!src/**/*.test.ts',
    '!src/index.ts',
  ],
  coverageThreshold: {
    global: {
      branches: 80,
      functions: 80,
      lines: 80,
      statements: 80,
    },
  },
};
