/**
 * Main Express application entry point
 */

import path from 'path';
import express, { Application, Request, Response, NextFunction } from 'express';
import cors from 'cors';
import config from './config';
import { readonlyGuard } from './middleware/readonly';
import { adminOnly } from './middleware/adminOnly';
import { requireAuth, optionalAuth } from './middleware/auth';
import productsRouter from './routes/products';
import datasheetsRouter from './routes/datasheets';
import uploadRouter from './routes/upload';
import subscriptionRouter from './routes/subscription';
import searchRouter from './routes/search';
import compatRouter from './routes/compat';
import relationsRouter from './routes/relations';
import docsRouter from './routes/docs';
import adminRouter from './routes/admin';
import authRouter from './routes/auth';
import projectsRouter from './routes/projects';
import { safeLog } from './util/log';

const app: Application = express();

// Security: don't leak server technology
app.disable('x-powered-by');

// Middleware
app.use(cors(config.cors));
app.use(express.json({ limit: '10mb' }));
app.use(express.urlencoded({ extended: true }));

// Request logging middleware
app.use((req: Request, _res: Response, next: NextFunction) => {
  // CR/LF strip inline (req.path is user-controlled). The .replace is the
  // CodeQL js/log-injection barrier; safeLog only formats/truncates.
  console.log(`[${config.appMode}] ${req.method} ${safeLog(req.path.replace(/\r|\n/g, ''))}`);
  next();
});

// Readonly guard — blocks writes in public mode
if (config.appMode === 'public') {
  console.log('[server] Public mode: write operations disabled');
  app.use('/api', readonlyGuard);
}

// Health check endpoint. `mode` reports Cognito group membership when
// the caller presents a valid token, otherwise falls back to the
// deploy-time APP_MODE flag (which is now a local-dev convenience —
// admin gating in production is on Cognito groups).
app.get('/health', optionalAuth, (req: Request, res: Response) => {
  const mode = req.user?.groups.includes('admin') ? 'admin' : config.appMode;
  res.json({
    status: 'healthy',
    timestamp: new Date().toISOString(),
    environment: config.nodeEnv,
    mode,
  });
});

// Upload route — available in both public and admin mode (queues only, no data mutation)
app.use('/api/upload', uploadRouter);

// Auth routes — register/login/etc. need POST in public mode. The
// readonly guard exempts /auth/* paths (see middleware/readonly.ts).
app.use('/api/auth', authRouter);

// Projects — per-user collections of product refs. Auth-gated inside
// the router; readonly-guard exempts /projects/* so logged-in users
// can mutate in public mode.
app.use('/api/projects', projectsRouter);

// API routes
app.use('/api/products', productsRouter);
app.use('/api/datasheets', datasheetsRouter);
app.use('/api/subscription', subscriptionRouter);
app.use('/api/v1/search', searchRouter);
app.use('/api/v1/compat', compatRouter);
// Device-relations API — typed compatibility queries (Phase 3b of
// SCHEMA Phase 3; see todo/SCHEMA.md Part 3 and todo/BUILD.md Part 4).
app.use('/api/v1/relations', relationsRouter);
app.use('/api/admin', requireAuth, adminOnly, adminRouter);
app.use('/api', docsRouter);

// Serve frontend static files in production (Docker container)
if (process.env.NODE_ENV === 'production') {
  const publicDir = path.join(__dirname, '..', '..', 'public');
  app.use(express.static(publicDir));
  app.get('*', (_req: Request, res: Response, next: NextFunction) => {
    if (_req.path.startsWith('/api/') || _req.path === '/health') return next();
    res.sendFile(path.join(publicDir, 'index.html'));
  });
}

// Root endpoint
app.get('/', (_req: Request, res: Response) => {
  res.json({
    name: 'Specodex API',
    version: '1.0.0',
    endpoints: {
      health: '/health',
      products: '/api/products',
      datasheets: '/api/datasheets',
      summary: '/api/products/summary',
      subscription: '/api/subscription',
      search: '/api/v1/search',
      openapi: '/api/openapi.json',
      docs: '/api/docs',
    },
  });
});

// 404 handler
app.use((_req: Request, res: Response) => {
  res.status(404).json({
    success: false,
    error: 'Endpoint not found',
  });
});

// Error handler
app.use((err: Error, _req: Request, res: Response, _next: NextFunction) => {
  // Malformed JSON body → 400. express.json in strict mode throws a
  // SyntaxError for primitives (e.g. `"foo"`, `null`, `42`), invalid JSON,
  // and the Content-Length mismatch case. All of those are client errors,
  // not server errors — returning 500 would mask real failures.
  const anyErr = err as Error & { type?: string; status?: number };
  if (
    anyErr instanceof SyntaxError ||
    anyErr.type === 'entity.parse.failed' ||
    anyErr.status === 400
  ) {
    res.status(400).json({
      success: false,
      error: 'Malformed JSON body',
    });
    return;
  }

  console.error('Error:', err);
  res.status(500).json({
    success: false,
    error: 'Internal server error',
    message: config.nodeEnv === 'development' ? err.message : undefined,
  });
});

// Start server (only if not imported)
if (require.main === module) {
  app.listen(config.port, () => {
    console.log(`
Specodex API Server
━━━━━━━━━━━━━━━━━━━━━━━━━━
Mode: ${config.appMode}
Environment: ${config.nodeEnv}
Port: ${config.port}
DynamoDB Table: ${config.dynamodb.tableName}
AWS Region: ${config.aws.region}
━━━━━━━━━━━━━━━━━━━━━━━━━━
Ready to accept connections!
    `);
  });
}

export default app;
