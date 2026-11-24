/**
 * The app must boot with NODE_ENV=production.
 *
 * Every other suite imports the app with NODE_ENV=test, so the
 * production-only block in src/index.ts (static files + SPA fallback)
 * never executes under jest. Express 5 parses route paths with
 * path-to-regexp 8, which rejects a bare '*'; the fallback route threw
 * while the module loaded, so the Lambda failed init and every request
 * to the deployed API returned 500 while the whole suite stayed green.
 */

import type { Application } from 'express';
import request from 'supertest';

jest.mock('../src/db/dynamodb');

function loadAppWithNodeEnv(nodeEnv: string): Application {
  const previous = process.env.NODE_ENV;
  process.env.NODE_ENV = nodeEnv;
  let app: Application | undefined;
  try {
    jest.isolateModules(() => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      app = require('../src/index').default;
    });
  } finally {
    process.env.NODE_ENV = previous;
  }
  if (!app) throw new Error('app did not load');
  return app;
}

describe('app boot with NODE_ENV=production', () => {
  let app: Application;

  let errorSpy: jest.SpyInstance;

  beforeAll(() => {
    app = loadAppWithNodeEnv('production');
    // sendFile of the absent index.html is logged by the error handler.
    errorSpy = jest.spyOn(console, 'error').mockImplementation(() => undefined);
  });

  afterAll(() => {
    errorSpy.mockRestore();
  });

  it('serves /health', async () => {
    const response = await request(app).get('/health');
    expect(response.status).toBe(200);
    expect(response.body).toHaveProperty('status', 'healthy');
  });

  it('leaves unknown /api paths to the JSON 404 handler', async () => {
    const response = await request(app).get('/api/nonexistent');
    expect(response.status).toBe(404);
    expect(response.headers['content-type']).toMatch(/json/);
    expect(response.body).toEqual({ success: false, error: 'Endpoint not found' });
  });

  it.each(['/', '/build', '/products/some/deep/path'])(
    'routes non-API path %s to the SPA fallback, not the API handlers',
    async (urlPath) => {
      // No frontend build exists under jest, so sendFile fails and the
      // error handler answers. The point is that the request reached the
      // fallback: it is neither the API root document nor the JSON 404.
      const response = await request(app).get(urlPath);
      expect(response.body).not.toHaveProperty('name', 'Specodex API');
      expect(response.body).not.toHaveProperty('error', 'Endpoint not found');
    }
  );
});
