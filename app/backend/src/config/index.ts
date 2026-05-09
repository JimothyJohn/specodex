/**
 * Configuration management for the backend
 *
 * Local dev: reads from .env file
 * Production (Lambda): reads secrets from AWS SSM Parameter Store
 */

import dotenv from 'dotenv';
import path from 'path';

// Load .env for local development only
if (process.env.NODE_ENV !== 'production') {
  dotenv.config({ path: path.resolve(__dirname, '../../../../.env') });
}

const stage = process.env.STAGE || 'dev';
const appMode = (process.env.APP_MODE || 'admin') as 'public' | 'admin';
const ssmPrefix = process.env.SSM_PREFIX || `/datasheetminer/${stage}`;

// CORS allowlist — explicit, never `'*'`. In production the Express app
// sits behind CloudFront same-origin (so CORS rarely fires); in local dev
// the Vite dev server on :5173 hits the backend on :3001 cross-origin.
// Set CORS_ORIGIN as a comma-separated list to override.
const DEFAULT_CORS_ORIGINS = [
  'http://localhost:5173',
  'http://localhost:3001',
  'http://127.0.0.1:5173',
  'http://127.0.0.1:3001',
  'https://specodex.com',
  'https://www.specodex.com',
];
const corsOrigins: string[] = process.env.CORS_ORIGIN
  ? process.env.CORS_ORIGIN.split(',').map(s => s.trim()).filter(Boolean)
  : DEFAULT_CORS_ORIGINS;

export const config = {
  stage,
  appMode,
  port: parseInt(process.env.PORT || '3001', 10),
  nodeEnv: process.env.NODE_ENV || 'development',
  aws: {
    region: process.env.AWS_REGION || 'us-east-1',
    accountId: process.env.AWS_ACCOUNT_ID,
  },
  dynamodb: {
    tableName: process.env.DYNAMODB_TABLE_NAME || `products-${stage}`,
  },
  s3: {
    uploadBucket: process.env.UPLOAD_BUCKET || `datasheetminer-uploads-${stage}`,
  },
  cors: {
    origin: corsOrigins,
    credentials: true,
  },
  stripe: {
    lambdaUrl: process.env.STRIPE_LAMBDA_URL || '',
  },
  cognito: {
    // Populated from env (.env in dev) or SSM (Lambda cold start). Empty
    // string when unset means the auth middleware bails with a 503; the
    // app stays bootable for stages where the auth stack hasn't deployed.
    userPoolId: process.env.COGNITO_USER_POOL_ID || '',
    userPoolClientId: process.env.COGNITO_USER_POOL_CLIENT_ID || '',
  },
  ssmPrefix,
};

/**
 * Load runtime config from SSM Parameter Store (production only).
 * Call once at Lambda cold start before handling requests.
 *
 * GEMINI_API_KEY is intentionally NOT fetched here — the deployed app
 * doesn't scrape datasheets, only reads already-extracted records from
 * DynamoDB. Scraping runs locally via `./Quickstart process`, which reads
 * the key from .env.
 */
export async function loadSsmSecrets(): Promise<void> {
  if (process.env.NODE_ENV !== 'production') return;

  const { SSMClient, GetParametersCommand } = await import('@aws-sdk/client-ssm');
  const ssm = new SSMClient({ region: config.aws.region });

  const paramNames = [
    `${ssmPrefix}/stripe-lambda-url`,
    `${ssmPrefix}/cognito/user-pool-id`,
    `${ssmPrefix}/cognito/user-pool-client-id`,
  ];

  try {
    const result = await ssm.send(new GetParametersCommand({
      Names: paramNames,
      WithDecryption: true,
    }));

    for (const param of result.Parameters || []) {
      // Match against the suffix so the cognito/* nesting works without
      // a dedicated case per parameter. `.pop()` over `.split('/')`
      // would only catch the leaf, missing the namespace.
      const name = param.Name || '';
      if (name.endsWith('/stripe-lambda-url')) {
        config.stripe.lambdaUrl = param.Value || '';
      } else if (name.endsWith('/cognito/user-pool-id')) {
        config.cognito.userPoolId = param.Value || '';
      } else if (name.endsWith('/cognito/user-pool-client-id')) {
        config.cognito.userPoolClientId = param.Value || '';
      }
    }

    if (result.InvalidParameters?.length) {
      console.warn('SSM parameters not found:', result.InvalidParameters);
    }
  } catch (err) {
    console.error('Failed to load SSM secrets:', err);
  }
}

export default config;
