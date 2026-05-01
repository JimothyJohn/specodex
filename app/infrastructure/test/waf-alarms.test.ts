/**
 * WafAlarms synth assertions.
 *
 * Critical-path checks: alarm metric dimensions match the
 * SiteWebAcl's metric names (the most common foot-gun — wrong
 * dimensions = flat-zero graph, alarm never fires).
 */

import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { WafAlarms } from '../lib/waf/waf-alarms';
import { SiteWebAclMetricNames } from '../lib/waf/site-web-acl';

const NAMES: SiteWebAclMetricNames = {
  webAcl: 'specodex-staging-edge-acl',
  readRateLimit: 'specodex-staging-read-rate-limit',
  authRateLimit: 'specodex-staging-auth-rate-limit',
  commonRules: 'specodex-staging-common-rules',
};

function templateFor(props: ConstructorParameters<typeof WafAlarms>[2]): Template {
  const app = new cdk.App();
  const stack = new cdk.Stack(app, 'TestAlarmsHost', {
    env: { account: '111111111111', region: 'us-east-1' },
  });
  new WafAlarms(stack, 'TestAlarms', props);
  return Template.fromStack(stack);
}

describe('WafAlarms', () => {
  it('creates an SNS topic', () => {
    const t = templateFor({ stage: 'staging', metricNames: NAMES });
    t.hasResourceProperties('AWS::SNS::Topic', {
      TopicName: 'specodex-staging-waf-alarms',
    });
  });

  it('subscribes the alarmEmail when provided', () => {
    const t = templateFor({
      stage: 'staging',
      metricNames: NAMES,
      alarmEmail: 'oncall@example.com',
    });
    t.hasResourceProperties('AWS::SNS::Subscription', {
      Protocol: 'email',
      Endpoint: 'oncall@example.com',
    });
  });

  it('skips email subscription when alarmEmail is unset', () => {
    const t = templateFor({ stage: 'staging', metricNames: NAMES });
    t.resourceCountIs('AWS::SNS::Subscription', 0);
  });

  it('emits four alarms (scrape, cred-stuffing, common-rules, total)', () => {
    const t = templateFor({ stage: 'staging', metricNames: NAMES });
    t.resourceCountIs('AWS::CloudWatch::Alarm', 4);
  });

  it('per-rule alarms reference Rule + WebACL dimensions matching the metricNames', () => {
    const t = templateFor({ stage: 'staging', metricNames: NAMES });
    t.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'specodex-staging-waf-scrape-attempt',
      Namespace: 'AWS/WAFV2',
      MetricName: 'BlockedRequests',
      Dimensions: Match.arrayWith([
        { Name: 'Region', Value: 'Global' },
        { Name: 'Rule', Value: 'specodex-staging-read-rate-limit' },
        { Name: 'WebACL', Value: 'specodex-staging-edge-acl' },
      ]),
    });
    t.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'specodex-staging-waf-credential-stuffing',
      Dimensions: Match.arrayWith([
        { Name: 'Rule', Value: 'specodex-staging-auth-rate-limit' },
      ]),
    });
    t.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'specodex-staging-waf-common-rules-spike',
      Dimensions: Match.arrayWith([
        { Name: 'Rule', Value: 'specodex-staging-common-rules' },
      ]),
    });
  });

  it('total-blocked alarm has no Rule dimension (catches managed-subset firing)', () => {
    const t = templateFor({ stage: 'staging', metricNames: NAMES });
    const alarms = Object.values(t.toJSON().Resources).filter(
      (r: any) => r.Type === 'AWS::CloudWatch::Alarm',
    ) as any[];
    const total = alarms.find(a => a.Properties.AlarmName === 'specodex-staging-waf-total-blocked');
    expect(total).toBeDefined();
    const dimNames = total.Properties.Dimensions.map((d: { Name: string }) => d.Name);
    expect(dimNames).not.toContain('Rule');
    expect(dimNames).toContain('WebACL');
  });

  it('uses Region=Global for CLOUDFRONT-scoped metrics', () => {
    const t = templateFor({ stage: 'staging', metricNames: NAMES });
    const alarms = Object.values(t.toJSON().Resources).filter(
      (r: any) => r.Type === 'AWS::CloudWatch::Alarm',
    ) as any[];
    for (const a of alarms) {
      const region = a.Properties.Dimensions.find((d: { Name: string }) => d.Name === 'Region');
      expect(region.Value).toBe('Global');
    }
  });

  it('respects custom thresholds', () => {
    const t = templateFor({
      stage: 'staging',
      metricNames: NAMES,
      scrapeThresholdPerMin: 250,
      credentialStuffingThresholdPerMin: 10,
    });
    t.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'specodex-staging-waf-scrape-attempt',
      Threshold: 250,
    });
    t.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'specodex-staging-waf-credential-stuffing',
      Threshold: 10,
    });
  });

  it('alarms wire SNS action on both ALARM and OK transitions', () => {
    const t = templateFor({ stage: 'staging', metricNames: NAMES });
    const alarms = Object.values(t.toJSON().Resources).filter(
      (r: any) => r.Type === 'AWS::CloudWatch::Alarm',
    ) as any[];
    for (const a of alarms) {
      expect(a.Properties.AlarmActions).toBeDefined();
      expect(a.Properties.AlarmActions.length).toBe(1);
      expect(a.Properties.OKActions).toBeDefined();
      expect(a.Properties.OKActions.length).toBe(1);
    }
  });

  it('treats missing data as not-breaching (no alarm storm during traffic gaps)', () => {
    const t = templateFor({ stage: 'staging', metricNames: NAMES });
    const alarms = Object.values(t.toJSON().Resources).filter(
      (r: any) => r.Type === 'AWS::CloudWatch::Alarm',
    ) as any[];
    for (const a of alarms) {
      expect(a.Properties.TreatMissingData).toBe('notBreaching');
    }
  });
});
