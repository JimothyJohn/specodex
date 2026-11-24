module.exports = {
  testEnvironment: 'node',
  roots: ['<rootDir>/test'],
  testMatch: ['**/*.test.ts'],
  transform: {
    // TypeScript 7 (native compiler) no longer exposes the API ts-jest
    // needs; use the JS-hosted @typescript/typescript6, same as
    // app/backend/jest.config.js.
    '^.+\\.tsx?$': ['ts-jest', { compiler: '@typescript/typescript6' }],
  },
};
