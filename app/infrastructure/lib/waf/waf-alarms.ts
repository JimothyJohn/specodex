/**
 * CloudWatch alarms on the WAF web ACL (todo/AUTH.md Phase 5b
 * "Layer 4 — visibility").
 *
 * Without alarms, the WAF is silent: you don't know if it's
 * blocking real attacks, over-blocking legitimate users, or just
 * sitting there inert because traffic never reaches the
 * thresholds. These four alarms cover the operational questions:
 *
 *   1. ScrapeAttempt — anonymous read rate limit blocking heavily.
 *      Either a real scraper hit, or the limit is too tight.
 *   2. CredentialStuffing — auth-flow rate limit blocking heavily.
 *      Pages because the worst case (bot success) is account
 *      compromise.
 *   3. CommonRuleSetSpike — managed common rules blocking heavily.
 *      Probably an injection attempt; could also be a legitimate
 *      caller hitting a false-positive rule (notify, manual
 *      review).
 *   4. EdgeAclTotal — total blocked across the whole ACL,
 *      catch-all. Spikes here without a corresponding spike in
 *      one of the above mean a managed-rule subset is firing.
 *
 * Alarms publish to a single SNS topic. ALARM_EMAIL env var
 * subscribes an email address; if unset, the topic exists but
 * has no subscriptions (set later via aws sns subscribe).
 *
 * Dimensions are CRITICAL: WAF/V2 metrics use the
 * `visibilityConfig.metricName` from the web ACL and each rule
 * (NOT the rule's `Name` field). Get this wrong and the alarm
 * shows a flat-zero graph. The metric names live on the
 * SiteWebAcl construct as `metricNames` for that reason.
 *
 * Region: CLOUDFRONT-scoped WAF metrics live in `Global` (not
 * `us-east-1`). Make sure cross-region console links open
 * correctly.
 */

import * as cdk from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cloudwatchActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as snsSubs from 'aws-cdk-lib/aws-sns-subscriptions';
import { Construct } from 'constructs';
import { SiteWebAclMetricNames } from './site-web-acl';

export interface WafAlarmsProps {
  stage: string;
  metricNames: SiteWebAclMetricNames;
  /** Page-level threshold for the read-rate-limit blocked count
   *  (per minute, summed over a 5-minute window). Default 100. */
  scrapeThresholdPerMin?: number;
  /** Page-level threshold for the auth-rate-limit blocked count
   *  (per minute). Default 30 — credential stuffing is rare and
   *  serious, so the bar is lower. */
  credentialStuffingThresholdPerMin?: number;
  /** Notify-level threshold for the common-rules blocked count
   *  (per minute). Default 50. */
  commonRulesThresholdPerMin?: number;
  /** Total-blocked across the ACL; catch-all. Default 500. */
  totalBlockedThresholdPerMin?: number;
  /** Email address to subscribe to the SNS topic. When unset, the
   *  topic exists with no subscriptions. */
  alarmEmail?: string;
}

const NS = 'AWS/WAFV2';

export class WafAlarms extends Construct {
  public readonly topic: sns.Topic;

  constructor(scope: Construct, id: string, props: WafAlarmsProps) {
    super(scope, id);

    this.topic = new sns.Topic(this, 'AlarmTopic', {
      topicName: `specodex-${props.stage}-waf-alarms`,
      displayName: `Specodex ${props.stage} WAF alarms`,
    });

    if (props.alarmEmail) {
      this.topic.addSubscription(new snsSubs.EmailSubscription(props.alarmEmail));
    }

    const action = new cloudwatchActions.SnsAction(this.topic);

    const ruleMetric = (ruleMetricName: string): cloudwatch.Metric =>
      new cloudwatch.Metric({
        namespace: NS,
        metricName: 'BlockedRequests',
        dimensionsMap: {
          // CLOUDFRONT-scoped ACLs surface metrics in 'Global', not
          // a regional name.
          Region: 'Global',
          WebACL: props.metricNames.webAcl,
          Rule: ruleMetricName,
        },
        statistic: 'Sum',
        period: cdk.Duration.minutes(1),
      });

    const aclTotal = new cloudwatch.Metric({
      namespace: NS,
      metricName: 'BlockedRequests',
      dimensionsMap: {
        Region: 'Global',
        WebACL: props.metricNames.webAcl,
      },
      statistic: 'Sum',
      period: cdk.Duration.minutes(1),
    });

    const scrapeAlarm = new cloudwatch.Alarm(this, 'ScrapeAttempt', {
      alarmName: `specodex-${props.stage}-waf-scrape-attempt`,
      alarmDescription: 'Anonymous read rate-limit is blocking heavily — likely a scrape attempt or the threshold is too tight.',
      metric: ruleMetric(props.metricNames.readRateLimit),
      threshold: props.scrapeThresholdPerMin ?? 100,
      evaluationPeriods: 5,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    const credAlarm = new cloudwatch.Alarm(this, 'CredentialStuffing', {
      alarmName: `specodex-${props.stage}-waf-credential-stuffing`,
      alarmDescription: 'Auth-flow rate-limit blocking heavily — likely credential stuffing in progress. PAGE.',
      metric: ruleMetric(props.metricNames.authRateLimit),
      threshold: props.credentialStuffingThresholdPerMin ?? 30,
      evaluationPeriods: 5,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    const commonAlarm = new cloudwatch.Alarm(this, 'CommonRuleSetSpike', {
      alarmName: `specodex-${props.stage}-waf-common-rules-spike`,
      alarmDescription: 'AWS managed common rules blocking heavily — injection attempt, or false-positive on a legitimate caller.',
      metric: ruleMetric(props.metricNames.commonRules),
      threshold: props.commonRulesThresholdPerMin ?? 50,
      evaluationPeriods: 5,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    const totalAlarm = new cloudwatch.Alarm(this, 'EdgeAclTotal', {
      alarmName: `specodex-${props.stage}-waf-total-blocked`,
      alarmDescription: 'Total blocked requests across the WAF ACL — catch-all. Spikes here without a corresponding spike in a per-rule alarm mean a managed subset is firing.',
      metric: aclTotal,
      threshold: props.totalBlockedThresholdPerMin ?? 500,
      evaluationPeriods: 5,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    for (const alarm of [scrapeAlarm, credAlarm, commonAlarm, totalAlarm]) {
      alarm.addAlarmAction(action);
      alarm.addOkAction(action);   // resolution notifications are useful
    }

    new cdk.CfnOutput(this, 'AlarmTopicArn', {
      value: this.topic.topicArn,
      description: 'SNS topic for WAF alarm notifications.',
    });
  }
}
