import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import { AppConfig } from './config';

export interface DatabaseStackProps extends cdk.StackProps {
  table?: dynamodb.ITable;
  uploadBucket?: s3.IBucket;
}

export class DatabaseStack extends cdk.Stack {
  public readonly table: dynamodb.Table;
  public readonly uploadBucket: s3.Bucket;

  constructor(scope: Construct, id: string, config: AppConfig, props?: cdk.StackProps) {
    super(scope, id, props);

    this.table = new dynamodb.Table(this, 'ProductsTable', {
      tableName: config.tableName,
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      // PITR on every stage — a bad migration on dev now has a 35-day
      // restore window instead of being unrecoverable. Already enabled
      // out-of-band on prod (verified 2026-05-07); declaring it in CDK
      // brings prod back into IaC parity.
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: true,
      },
      // Belt and suspenders for prod: even with RETAIN removal policy,
      // an explicit DeleteTable API call would still drop the table.
      // deletionProtection refuses that until the flag is flipped off.
      deletionProtection: config.stage === 'prod',
      removalPolicy: config.stage === 'prod'
        ? cdk.RemovalPolicy.RETAIN
        : cdk.RemovalPolicy.DESTROY,
    });

    this.uploadBucket = new s3.Bucket(this, 'UploadBucket', {
      bucketName: `datasheetminer-uploads-${config.stage}-${config.env.account}`,
      removalPolicy: config.stage === 'prod'
        ? cdk.RemovalPolicy.RETAIN
        : cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: config.stage !== 'prod',
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      lifecycleRules: [
        { prefix: 'done/', expiration: cdk.Duration.days(90) },
      ],
      cors: [
        {
          allowedMethods: [s3.HttpMethods.PUT, s3.HttpMethods.POST],
          allowedOrigins: ['*'],
          allowedHeaders: ['*'],
          maxAge: 3600,
        },
      ],
    });

    new cdk.CfnOutput(this, 'TableName', { value: this.table.tableName });
    new cdk.CfnOutput(this, 'UploadBucketName', { value: this.uploadBucket.bucketName });
  }
}
