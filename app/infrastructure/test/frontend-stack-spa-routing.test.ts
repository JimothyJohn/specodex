/**
 * SPA routing must not swallow API errors.
 *
 * Regression for /build showing `Unexpected token '<'`: distribution-
 * level errorResponses rewrote every 403/404 — including /api/* JSON
 * 404s from the Lambda — into a 200 index.html. SPA routing now lives
 * in a viewer-request function on the S3 behavior only.
 */

import * as fs from 'fs';
import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { FrontendStack } from '../lib/frontend-stack';
import { SPA_REWRITE_FUNCTION_CODE } from '../lib/edge/spa-rewrite';

const distDir = path.join(__dirname, '../../frontend/dist');
let createdDist = false;

beforeAll(() => {
  if (!fs.existsSync(distDir)) {
    fs.mkdirSync(distDir, { recursive: true });
    fs.writeFileSync(path.join(distDir, 'index.html'), '<!-- test placeholder -->');
    createdDist = true;
  }
});

afterAll(() => {
  if (createdDist) fs.rmSync(distDir, { recursive: true, force: true });
});

function template(): Template {
  const app = new cdk.App();
  const env = { account: '111111111111', region: 'us-east-1' };
  const apiStack = new cdk.Stack(app, 'TestApiHost', { env });
  const api = new apigwv2.HttpApi(apiStack, 'TestApi');
  const stack = new FrontendStack(
    app,
    'TestFrontend',
    {
      stage: 'dev',
      env,
      tableName: 'products-dev',
      ssmPrefix: '/datasheetminer/dev',
    },
    { env, api },
  );
  return Template.fromStack(stack);
}

describe('FrontendStack SPA routing', () => {
  it('has no distribution-wide error rewrites (they would mask /api errors)', () => {
    template().hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({ CustomErrorResponses: Match.absent() }),
    });
  });

  it('attaches the SPA rewrite to the default (S3) behavior only', () => {
    const t = template();
    const dist = Object.values(t.findResources('AWS::CloudFront::Distribution'))[0] as {
      Properties: { DistributionConfig: Record<string, any> };
    };
    const cfg = dist.Properties.DistributionConfig;
    expect(cfg.DefaultCacheBehavior.FunctionAssociations).toHaveLength(1);
    expect(cfg.DefaultCacheBehavior.FunctionAssociations[0].EventType).toBe('viewer-request');
    for (const b of cfg.CacheBehaviors) {
      expect(b.FunctionAssociations).toBeUndefined();
    }
    expect(cfg.CacheBehaviors.map((b: { PathPattern: string }) => b.PathPattern)).toContain(
      '/api/*',
    );
  });
});

describe('SPA rewrite function', () => {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const handler = new Function(`${SPA_REWRITE_FUNCTION_CODE}; return handler;`)() as (e: {
    request: { uri: string };
  }) => { uri: string };
  const rewrite = (uri: string) => handler({ request: { uri } }).uri;

  it.each(['/', '/build', '/projects/0a69acc3-1275-53ff-9ac4-52d1fb2fecbe', '/welcome/'])(
    'routes %s to index.html',
    uri => expect(rewrite(uri)).toBe('/index.html'),
  );

  it.each(['/assets/index-B1frycvV.js', '/robots.txt', '/sitemap.xml', '/manifest.json'])(
    'passes file %s through',
    uri => expect(rewrite(uri)).toBe(uri),
  );
});
