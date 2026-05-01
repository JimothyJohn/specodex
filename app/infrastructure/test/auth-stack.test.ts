/**
 * AuthStack synth assertions.
 *
 * Synthesizes AuthStack in isolation (skipping bin/app.ts so we
 * don't need backend/frontend dist artifacts) and asserts the
 * Cognito EmailConfiguration matches the SES vs. default-sender
 * conditional in lib/auth/auth-stack.ts.
 */

import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { AuthStack } from '../lib/auth/auth-stack';
import { AppConfig } from '../lib/config';

function baseConfig(): AppConfig {
  return {
    stage: 'dev',
    env: { account: '111111111111', region: 'us-east-1' },
    tableName: 'products-dev',
    ssmPrefix: '/datasheetminer/dev',
  };
}

describe('AuthStack — Cognito EmailConfiguration', () => {
  it('falls back to default Cognito sender when ses config is absent', () => {
    const app = new cdk.App();
    const stack = new AuthStack(app, 'TestAuth', baseConfig(), {
      env: { account: '111111111111', region: 'us-east-1' },
    });
    const template = Template.fromStack(stack);
    template.hasResourceProperties('AWS::Cognito::UserPool', {
      EmailConfiguration: {
        EmailSendingAccount: 'COGNITO_DEFAULT',
      },
    });
  });

  it('wires SES sender when ses config is set', () => {
    const app = new cdk.App();
    const config: AppConfig = {
      ...baseConfig(),
      ses: {
        fromEmail: 'noreply@advin.io',
        fromName: 'Specodex',
        replyTo: 'support@advin.io',
        verifiedDomain: 'advin.io',
      },
    };
    const stack = new AuthStack(app, 'TestAuthSes', config, {
      env: { account: '111111111111', region: 'us-east-1' },
    });
    const template = Template.fromStack(stack);
    template.hasResourceProperties('AWS::Cognito::UserPool', {
      EmailConfiguration: {
        EmailSendingAccount: 'DEVELOPER',
        From: 'Specodex <noreply@advin.io>',
        ReplyToEmailAddress: 'support@advin.io',
        // SourceArn is a Fn::Join token at synth time; assert the
        // identity/<domain> string appears in the join's segments.
        SourceArn: {
          'Fn::Join': Match.arrayWith([
            Match.arrayWith([Match.stringLikeRegexp(':ses:us-east-1:111111111111:identity/advin\\.io$')]),
          ]),
        },
      },
    });
  });

  it('ships custom verification + invitation email templates regardless of sender', () => {
    const app = new cdk.App();
    const stack = new AuthStack(app, 'TestAuthTemplates', baseConfig(), {
      env: { account: '111111111111', region: 'us-east-1' },
    });
    const template = Template.fromStack(stack);
    template.hasResourceProperties('AWS::Cognito::UserPool', {
      VerificationMessageTemplate: {
        DefaultEmailOption: 'CONFIRM_WITH_CODE',
        EmailSubject: 'Verify your Specodex account',
        EmailMessage: Match.stringLikeRegexp('Specodex verification code is \\{####\\}'),
      },
      AdminCreateUserConfig: {
        InviteMessageTemplate: {
          EmailSubject: 'Welcome to Specodex',
          EmailMessage: Match.stringLikeRegexp('Hi \\{username\\}'),
        },
      },
    });
  });

  it('still creates the admin group + SSM params (regression check)', () => {
    const app = new cdk.App();
    const stack = new AuthStack(app, 'TestAuthRegression', baseConfig(), {
      env: { account: '111111111111', region: 'us-east-1' },
    });
    const template = Template.fromStack(stack);
    template.hasResourceProperties('AWS::Cognito::UserPoolGroup', {
      GroupName: 'admin',
      Precedence: 1,
    });
    template.resourceCountIs('AWS::SSM::Parameter', 2);
  });
});
