/**
 * WAFv2 web ACL for the CloudFront distribution (todo/AUTH.md
 * Phase 5b — bot protection at scale, edge layer).
 *
 * Three rule classes, in priority order:
 *
 *   1. AnonymousReadRateLimit (priority 10) — rate-based per IP for
 *      /api/products + /api/v1/search. The data is the entire
 *      product; without this, anyone can pull the whole catalog with
 *      a 5-line script. 600 req / 5min / IP is generous for browsers
 *      (≈2/sec sustained) and brutal for scrapers. When per-key API
 *      auth lands (todo/API.md), authed callers will be exempted via
 *      a notStatement on the Authorization header — for now, the
 *      limit applies uniformly because we have ~zero authed read
 *      callers to penalize.
 *
 *   2. AuthFlowRateLimit (priority 20) — rate-based per IP for
 *      /api/auth/login + /api/auth/register. 60 req / 5min / IP.
 *      Blunts credential stuffing (slow-roll bots that respect
 *      timing get blocked at Cognito's own login flood; bots that
 *      don't get blocked here).
 *
 *   3. AWSManagedRulesCommonRuleSet (priority 30) — SQLi, XSS,
 *      oversized body, etc. AWS-managed, free vendor rule group.
 *
 * Bot Control common-tier is plumbed but defaults off because it
 * costs $10/month + $1 per 1M inspected requests. Toggle via
 * WAF_BOT_CONTROL_ENABLED=true once the basic limits prove
 * insufficient.
 *
 * Scoping: CLOUDFRONT, must be deployed to us-east-1 regardless of
 * where the rest of the stack lives. The repo is single-region
 * us-east-1 today, so this is a no-op constraint.
 */

import * as cdk from 'aws-cdk-lib';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';
import { Construct } from 'constructs';

export interface SiteWebAclProps {
  /** Stage name — appears in metric names so CloudWatch can split
   *  staging from prod. */
  stage: string;
  /** Anonymous read rate limit, requests per 5min per IP. Default 600. */
  readRateLimit?: number;
  /** Auth-flow rate limit, requests per 5min per IP. Default 60. */
  authRateLimit?: number;
  /** Enable the AWS Managed Bot Control (Common tier). Off by
   *  default — the rule group adds ~$10/month + $1/1M requests
   *  inspected. Turn on once basic limits prove insufficient. */
  botControlEnabled?: boolean;
}

/**
 * Path-prefix byte-match statement helper. WAFv2 doesn't have a
 * native "path startsWith" — we build it from a byteMatchStatement
 * over the uriPath FieldToMatch.
 */
function pathStartsWith(prefix: string): wafv2.CfnWebACL.StatementProperty {
  return {
    byteMatchStatement: {
      fieldToMatch: { uriPath: {} },
      positionalConstraint: 'STARTS_WITH',
      searchString: prefix,
      textTransformations: [{ priority: 0, type: 'NONE' }],
    },
  };
}

/**
 * The CloudWatch metric names this construct emits, exposed as a
 * stable shape so alarms (Phase 5f) can reference them without
 * restating the format string.
 *
 * The `WebACL` and `Rule` dimensions on AWS/WAFV2 metrics use the
 * `metricName` from each `visibilityConfig`, not the rule's `Name`
 * field. Get this wrong and CloudWatch silently shows a flat-zero
 * graph. The shape below is the source of truth.
 */
export interface SiteWebAclMetricNames {
  webAcl: string;
  readRateLimit: string;
  authRateLimit: string;
  commonRules: string;
  botControl?: string;
}

export class SiteWebAcl extends Construct {
  public readonly webAcl: wafv2.CfnWebACL;
  public readonly metricNames: SiteWebAclMetricNames;

  constructor(scope: Construct, id: string, props: SiteWebAclProps) {
    super(scope, id);

    const readLimit = props.readRateLimit ?? 600;
    const authLimit = props.authRateLimit ?? 60;
    const stage = props.stage;

    const rules: wafv2.CfnWebACL.RuleProperty[] = [
      {
        name: 'AnonymousReadRateLimit',
        priority: 10,
        action: { block: {} },
        statement: {
          rateBasedStatement: {
            limit: readLimit,
            aggregateKeyType: 'IP',
            scopeDownStatement: {
              orStatement: {
                statements: [
                  pathStartsWith('/api/products'),
                  pathStartsWith('/api/v1/search'),
                ],
              },
            },
          },
        },
        visibilityConfig: {
          cloudWatchMetricsEnabled: true,
          metricName: `specodex-${stage}-read-rate-limit`,
          sampledRequestsEnabled: true,
        },
      },
      {
        name: 'AuthFlowRateLimit',
        priority: 20,
        action: { block: {} },
        statement: {
          rateBasedStatement: {
            limit: authLimit,
            aggregateKeyType: 'IP',
            scopeDownStatement: {
              orStatement: {
                statements: [
                  pathStartsWith('/api/auth/login'),
                  pathStartsWith('/api/auth/register'),
                ],
              },
            },
          },
        },
        visibilityConfig: {
          cloudWatchMetricsEnabled: true,
          metricName: `specodex-${stage}-auth-rate-limit`,
          sampledRequestsEnabled: true,
        },
      },
      {
        name: 'AWSManagedRulesCommonRuleSet',
        priority: 30,
        // overrideAction.none = use the rule group's actions as
        // configured (block by default for the common rules).
        overrideAction: { none: {} },
        statement: {
          managedRuleGroupStatement: {
            vendorName: 'AWS',
            name: 'AWSManagedRulesCommonRuleSet',
          },
        },
        visibilityConfig: {
          cloudWatchMetricsEnabled: true,
          metricName: `specodex-${stage}-common-rules`,
          sampledRequestsEnabled: true,
        },
      },
    ];

    if (props.botControlEnabled) {
      rules.push({
        name: 'AWSManagedRulesBotControlRuleSet',
        priority: 40,
        overrideAction: { none: {} },
        statement: {
          managedRuleGroupStatement: {
            vendorName: 'AWS',
            name: 'AWSManagedRulesBotControlRuleSet',
            managedRuleGroupConfigs: [
              { awsManagedRulesBotControlRuleSet: { inspectionLevel: 'COMMON' } },
            ],
          },
        },
        visibilityConfig: {
          cloudWatchMetricsEnabled: true,
          metricName: `specodex-${stage}-bot-control`,
          sampledRequestsEnabled: true,
        },
      });
    }

    this.webAcl = new wafv2.CfnWebACL(this, 'WebAcl', {
      name: `specodex-${stage}-edge-acl`,
      scope: 'CLOUDFRONT',
      defaultAction: { allow: {} },
      rules,
      visibilityConfig: {
        cloudWatchMetricsEnabled: true,
        metricName: `specodex-${stage}-edge-acl`,
        sampledRequestsEnabled: true,
      },
    });

    this.metricNames = {
      webAcl: `specodex-${stage}-edge-acl`,
      readRateLimit: `specodex-${stage}-read-rate-limit`,
      authRateLimit: `specodex-${stage}-auth-rate-limit`,
      commonRules: `specodex-${stage}-common-rules`,
      ...(props.botControlEnabled ? { botControl: `specodex-${stage}-bot-control` } : {}),
    };

    new cdk.CfnOutput(this, 'WebAclArn', {
      value: this.webAcl.attrArn,
      description: 'WAFv2 web ACL ARN attached to the CloudFront distribution.',
    });
  }

  public get arn(): string {
    return this.webAcl.attrArn;
  }
}
