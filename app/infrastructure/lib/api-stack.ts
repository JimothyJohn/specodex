import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigw from 'aws-cdk-lib/aws-apigatewayv2';
import * as apigwIntegrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { AppConfig } from './config';
import * as path from 'path';

interface ApiStackProps extends cdk.StackProps {
  table: dynamodb.ITable;
  uploadBucket: s3.IBucket;
}

export class ApiStack extends cdk.Stack {
  public readonly api: apigw.HttpApi;

  constructor(scope: Construct, id: string, config: AppConfig, props: ApiStackProps) {
    super(scope, id, props);

    const { table, uploadBucket } = props;

    // Lambda function (backend API)
    const handler = new lambda.Function(this, 'ApiHandler', {
      functionName: `specodex-api-${config.stage}`,
      runtime: lambda.Runtime.NODEJS_22_X,
      handler: 'lambda.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../backend/dist')),
      memorySize: 512,
      timeout: cdk.Duration.seconds(30),
      environment: {
        NODE_ENV: 'production',
        STAGE: config.stage,
        APP_MODE: 'public',
        DYNAMODB_TABLE_NAME: table.tableName,
        UPLOAD_BUCKET: uploadBucket.bucketName,
        AWS_ACCOUNT_ID: config.env.account,
        SSM_PREFIX: config.ssmPrefix,
      },
    });

    table.grantReadWriteData(handler);
    uploadBucket.grantReadWrite(handler);

    // SSM read access for runtime config (stripe-lambda-url). GEMINI_API_KEY
    // is not provisioned in SSM — the deployed app doesn't scrape; scraping
    // runs locally via the Python CLI with the key in .env.
    handler.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ssm:GetParameter', 'ssm:GetParameters'],
      resources: [
        `arn:aws:ssm:${config.env.region}:${config.env.account}:parameter${config.ssmPrefix}/*`,
      ],
    }));

    // HTTP API
    this.api = new apigw.HttpApi(this, 'HttpApi', {
      apiName: `specodex-${config.stage}`,
      corsPreflight: {
        allowOrigins: ['*'],
        allowMethods: [apigw.CorsHttpMethod.ANY],
        allowHeaders: ['*'],
      },
    });

    const integration = new apigwIntegrations.HttpLambdaIntegration('LambdaIntegration', handler);
    this.api.addRoutes({
      path: '/{proxy+}',
      methods: [apigw.HttpMethod.ANY],
      integration,
    });

    new cdk.CfnOutput(this, 'ApiUrl', { value: this.api.url || '' });
  }
}
