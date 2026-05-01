/**
 * Cognito user pool for Specodex auth (todo/AUTH.md Phases 1 + 5a).
 *
 * What it provisions:
 *   - UserPool with email-as-username, self-signup, email
 *     verification, 12-char password policy.
 *   - UserPoolClient (public SPA client, no secret) configured for
 *     USER_PASSWORD_AUTH so the backend can proxy login from
 *     `POST /api/auth/login`.
 *   - "admin" group — replaces the binary `APP_MODE=admin` env gate.
 *   - Custom verification + invitation email templates so the user-
 *     facing copy says "Specodex" instead of Cognito's defaults.
 *   - SES sender when SES_FROM_EMAIL + SES_VERIFIED_DOMAIN are set
 *     (Phase 5a); falls back to default Cognito sender otherwise. The
 *     fallback exists so local synth and dev deploys don't require
 *     SES to be set up.
 *   - SSM parameters under `${ssmPrefix}/cognito/*` so the API
 *     Lambda's existing `loadSsmSecrets()` path can read them.
 *   - CfnOutputs mirroring the existing stack convention.
 */

import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';
import { AppConfig } from '../config';

export class AuthStack extends cdk.Stack {
  public readonly userPool: cognito.UserPool;
  public readonly userPoolClient: cognito.UserPoolClient;

  constructor(scope: Construct, id: string, config: AppConfig, props?: cdk.StackProps) {
    super(scope, id, props);

    // Phase 5a: SES sender when configured, else the default Cognito
    // sender. Default is sandbox-capped at 50 emails/day — fine for
    // local synth and CI dev deploys, insufficient for any stage that
    // sees real signup traffic. See todo/AUTH.md "SES verified
    // identity" for the verify-domain + DKIM + sandbox-exit steps.
    const userPoolEmail = config.ses
      ? cognito.UserPoolEmail.withSES({
          fromEmail: config.ses.fromEmail,
          fromName: config.ses.fromName,
          replyTo: config.ses.replyTo,
          sesVerifiedDomain: config.ses.verifiedDomain,
        })
      : cognito.UserPoolEmail.withCognito();

    this.userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: `specodex-${config.stage}`,
      signInAliases: { email: true, username: false },
      autoVerify: { email: true },
      selfSignUpEnabled: true,
      standardAttributes: {
        email: { required: true, mutable: false },
      },
      passwordPolicy: {
        minLength: 12,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: false,
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      email: userPoolEmail,
      // Custom message templates ship regardless of which sender is
      // active. Tokens — {####} for the code, {username} for the
      // username — are Cognito placeholders, not template literals.
      userVerification: {
        emailSubject: 'Verify your Specodex account',
        emailBody: 'Your Specodex verification code is {####}. It expires in 24 hours.',
        emailStyle: cognito.VerificationEmailStyle.CODE,
      },
      userInvitation: {
        emailSubject: 'Welcome to Specodex',
        emailBody: 'Hi {username}, an admin has created a Specodex account for you. Your temporary password is {####} — please sign in and change it.',
      },
      removalPolicy: config.stage === 'prod'
        ? cdk.RemovalPolicy.RETAIN
        : cdk.RemovalPolicy.DESTROY,
    });

    new cognito.CfnUserPoolGroup(this, 'AdminGroup', {
      userPoolId: this.userPool.userPoolId,
      groupName: 'admin',
      description: 'Users with admin privileges (replaces APP_MODE=admin gate).',
      precedence: 1,
    });

    this.userPoolClient = new cognito.UserPoolClient(this, 'WebClient', {
      userPool: this.userPool,
      userPoolClientName: `specodex-web-${config.stage}`,
      generateSecret: false,
      authFlows: {
        userPassword: true,
        userSrp: true,
      },
      preventUserExistenceErrors: true,
      accessTokenValidity: cdk.Duration.hours(1),
      idTokenValidity: cdk.Duration.hours(1),
      refreshTokenValidity: cdk.Duration.days(30),
      enableTokenRevocation: true,
    });

    new ssm.StringParameter(this, 'UserPoolIdParam', {
      parameterName: `${config.ssmPrefix}/cognito/user-pool-id`,
      stringValue: this.userPool.userPoolId,
      description: 'Cognito user pool ID for the Specodex API Lambda.',
    });

    new ssm.StringParameter(this, 'UserPoolClientIdParam', {
      parameterName: `${config.ssmPrefix}/cognito/user-pool-client-id`,
      stringValue: this.userPoolClient.userPoolClientId,
      description: 'Cognito web client ID for the Specodex SPA.',
    });

    new cdk.CfnOutput(this, 'UserPoolId', { value: this.userPool.userPoolId });
    new cdk.CfnOutput(this, 'UserPoolClientId', { value: this.userPoolClient.userPoolClientId });
  }
}
