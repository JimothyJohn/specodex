/**
 * CDK deployment configuration
 * Reads from environment variables (set by CI or local shell)
 */

export interface DomainConfig {
  domainName: string;
  certificateArn: string;
  hostedZoneName: string;
}

export interface SesConfig {
  /** Sender address. Must be on a verified SES domain (or a verified
   *  individual address while still in the SES sandbox). */
  fromEmail: string;
  /** Display name shown alongside fromEmail. */
  fromName?: string;
  /** Reply-to header. Defaults to fromEmail. */
  replyTo?: string;
  /** The SES-verified domain that owns fromEmail. CDK uses this to
   *  build the Cognito EmailConfiguration's SourceArn. */
  verifiedDomain: string;
}

export interface AppConfig {
  stage: string;
  env: { account: string; region: string };
  tableName: string;
  domain?: DomainConfig;
  /** SES sender for Cognito emails. When undefined, AuthStack falls
   *  back to the default Cognito sender (50 emails/day sandbox cap —
   *  fine for dev, insufficient for any real signup volume). */
  ses?: SesConfig;
  ssmPrefix: string;
}

export function getConfig(): AppConfig {
  const stage = process.env.STAGE || 'dev';
  const account = process.env.AWS_ACCOUNT_ID || process.env.CDK_DEFAULT_ACCOUNT || '';
  const region = process.env.AWS_REGION || process.env.CDK_DEFAULT_REGION || 'us-east-1';

  if (!account) {
    throw new Error('AWS_ACCOUNT_ID or CDK_DEFAULT_ACCOUNT must be set');
  }

  const domainName = process.env.DOMAIN_NAME;
  const certificateArn = process.env.CERTIFICATE_ARN;
  let domain: DomainConfig | undefined;
  if (domainName && certificateArn) {
    // HOSTED_ZONE_NAME is optional. For a 3+-part subdomain we default to
    // the parent (datasheets.advin.io → advin.io). For a 2-part apex
    // (specodex.com) the parent would be `com`, which fromLookup can't
    // resolve — fall back to the domain itself instead. `||` (not `??`)
    // so that an empty string from an unset GitHub Actions secret also
    // falls through to the default.
    const parts = domainName.split('.');
    const hostedZoneName =
      process.env.HOSTED_ZONE_NAME ||
      (parts.length > 2 ? parts.slice(1).join('.') : domainName);
    domain = { domainName, certificateArn, hostedZoneName };
  }

  // SES is opt-in: if SES_FROM_EMAIL is set we wire Cognito to send via
  // SES, otherwise the user pool keeps the default Cognito sender. This
  // lets local dev and unattended CI synth without forcing every
  // contributor to set up SES first. Production must set the env vars.
  const sesFromEmail = process.env.SES_FROM_EMAIL;
  const sesVerifiedDomain = process.env.SES_VERIFIED_DOMAIN;
  let ses: SesConfig | undefined;
  if (sesFromEmail && sesVerifiedDomain) {
    ses = {
      fromEmail: sesFromEmail,
      fromName: process.env.SES_FROM_NAME || undefined,
      replyTo: process.env.SES_REPLY_TO || undefined,
      verifiedDomain: sesVerifiedDomain,
    };
  } else if (sesFromEmail || sesVerifiedDomain) {
    // Half-set is almost always a misconfiguration — fail loud rather
    // than silently fall back to default-sender on prod.
    throw new Error(
      'SES_FROM_EMAIL and SES_VERIFIED_DOMAIN must both be set, or both unset.',
    );
  }

  return {
    stage,
    env: { account, region },
    tableName: process.env.DYNAMODB_TABLE_NAME || `products-${stage}`,
    domain,
    ses,
    ssmPrefix: `/datasheetminer/${stage}`,
  };
}
