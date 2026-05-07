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

    // Two Lambdas, same code, IAM split by HTTP method:
    //   GET / HEAD            → readHandler (table grantReadData, bucket grantRead)
    //   POST / PUT / PATCH /  → handler     (table grantReadWriteData, bucket grantReadWrite)
    //   DELETE
    // The Express app already gates writes via APP_MODE='public' + a
    // readonlyGuard middleware. The IAM split is the second layer:
    // even if a handler bug bypasses the app-level guard, the IAM
    // policy on the read Lambda physically can't write.

    const sharedLambdaProps: lambda.FunctionProps = {
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
    };

    const handler = new lambda.Function(this, 'ApiHandler', {
      ...sharedLambdaProps,
      functionName: `specodex-api-${config.stage}`,
    });

    const readHandler = new lambda.Function(this, 'ApiReadHandler', {
      ...sharedLambdaProps,
      functionName: `specodex-api-read-${config.stage}`,
    });

    table.grantReadWriteData(handler);
    uploadBucket.grantReadWrite(handler);

    table.grantReadData(readHandler);
    uploadBucket.grantRead(readHandler);

    // Both Lambdas need SSM read access for runtime config
    // (stripe-lambda-url etc.). GEMINI_API_KEY is not provisioned in SSM —
    // the deployed app doesn't scrape; scraping runs locally via the
    // Python CLI with the key in .env.
    const ssmPolicy = new iam.PolicyStatement({
      actions: ['ssm:GetParameter', 'ssm:GetParameters'],
      resources: [
        `arn:aws:ssm:${config.env.region}:${config.env.account}:parameter${config.ssmPrefix}/*`,
      ],
    });
    handler.addToRolePolicy(ssmPolicy);
    readHandler.addToRolePolicy(ssmPolicy);

    // HTTP API
    this.api = new apigw.HttpApi(this, 'HttpApi', {
      apiName: `specodex-${config.stage}`,
      corsPreflight: {
        allowOrigins: ['*'],
        allowMethods: [apigw.CorsHttpMethod.ANY],
        allowHeaders: ['*'],
      },
    });

    const writeIntegration = new apigwIntegrations.HttpLambdaIntegration('LambdaIntegration', handler);
    const readIntegration = new apigwIntegrations.HttpLambdaIntegration('ReadLambdaIntegration', readHandler);

    this.api.addRoutes({
      path: '/{proxy+}',
      methods: [apigw.HttpMethod.GET, apigw.HttpMethod.HEAD],
      integration: readIntegration,
    });
    this.api.addRoutes({
      path: '/{proxy+}',
      methods: [
        apigw.HttpMethod.POST,
        apigw.HttpMethod.PUT,
        apigw.HttpMethod.PATCH,
        apigw.HttpMethod.DELETE,
      ],
      integration: writeIntegration,
    });

    new cdk.CfnOutput(this, 'ApiUrl', { value: this.api.url || '' });
  }
}
