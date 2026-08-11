/**
 * FrontendStack WAF gating synth assertions.
 *
 * The edge WAF is prod-only by default (standing fees vs. near-zero
 * dev/staging traffic — 2026-08 cost audit); WAF_ENABLED overrides in
 * both directions. Synths the real FrontendStack so the gate is tested
 * where it lives, not re-implemented in the test.
 */

import * as fs from 'fs';
import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import { Template } from 'aws-cdk-lib/assertions';
import { FrontendStack } from '../lib/frontend-stack';
import { AppConfig } from '../lib/config';

// BucketDeployment stages ../../frontend/dist as an asset at synth
// time; CI runs infra tests without a frontend build, so provide a
// placeholder only when the real build output is absent.
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
  if (createdDist) {
    fs.rmSync(distDir, { recursive: true, force: true });
  }
});

const ENV_KEYS = ['WAF_ENABLED', 'WAF_BOT_CONTROL_ENABLED', 'WAF_ALARMS_ENABLED'] as const;
const savedEnv: Record<string, string | undefined> = {};

beforeEach(() => {
  for (const k of ENV_KEYS) {
    savedEnv[k] = process.env[k];
    delete process.env[k];
  }
});

afterEach(() => {
  for (const k of ENV_KEYS) {
    if (savedEnv[k] === undefined) delete process.env[k];
    else process.env[k] = savedEnv[k];
  }
});

function configFor(stage: string): AppConfig {
  return {
    stage,
    env: { account: '111111111111', region: 'us-east-1' },
    tableName: `products-${stage}`,
    ssmPrefix: `/datasheetminer/${stage}`,
  };
}

function templateFor(stage: string): Template {
  const app = new cdk.App();
  const env = { account: '111111111111', region: 'us-east-1' };
  const apiStack = new cdk.Stack(app, 'TestApiHost', { env });
  const api = new apigwv2.HttpApi(apiStack, 'TestApi');
  const stack = new FrontendStack(app, 'TestFrontend', configFor(stage), { env, api });
  return Template.fromStack(stack);
}

describe('FrontendStack WAF gating', () => {
  it('attaches the edge ACL on prod by default', () => {
    const t = templateFor('prod');
    t.resourceCountIs('AWS::WAFv2::WebACL', 1);
  });

  it('omits the edge ACL (and its alarms) on dev by default', () => {
    const t = templateFor('dev');
    t.resourceCountIs('AWS::WAFv2::WebACL', 0);
    t.resourceCountIs('AWS::CloudWatch::Alarm', 0);
  });

  it('omits the edge ACL on staging by default', () => {
    const t = templateFor('staging');
    t.resourceCountIs('AWS::WAFv2::WebACL', 0);
  });

  it('WAF_ENABLED=true forces the ACL on for non-prod stages', () => {
    process.env.WAF_ENABLED = 'true';
    const t = templateFor('staging');
    t.resourceCountIs('AWS::WAFv2::WebACL', 1);
  });

  it('WAF_ENABLED=false remains the break-glass off switch on prod', () => {
    process.env.WAF_ENABLED = 'false';
    const t = templateFor('prod');
    t.resourceCountIs('AWS::WAFv2::WebACL', 0);
  });
});
