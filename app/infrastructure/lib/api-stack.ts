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
import * as fs from 'fs';

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

    // ── Phase 1.3 (todo/PYTHON_BACKEND.md) — Python FastAPI Lambda
    // mounted at /api/v2/*. Both stacks share table, bucket, SSM.
    //
    // The Python Lambda code is built into `app/backend_py/dist/` by
    // `scripts/build_backend_py.sh` (run via
    // `./Quickstart build-backend-py`). The CDK asset points at that
    // pre-built directory — synth + diff don't need Docker, which is
    // important for CI workflows that don't have Docker available.
    // The build script DOES need Docker (or a matching Python 3.12
    // runtime locally) so the Lambda's deps are the right wheels.
    //
    // If `app/backend_py/dist/` doesn't exist (or is empty), the
    // Python Lambda construct is skipped entirely. v1 stays live;
    // v2 routes 404. The deploy script must run the build first when
    // deploying the v2 stack — `./Quickstart deploy` chains
    // `build-backend-py` before `cdk deploy`.
    const pyDistPath = path.join(__dirname, '../../backend_py/dist');
    const pyDistReady =
      fs.existsSync(pyDistPath) &&
      fs.existsSync(path.join(pyDistPath, 'app', 'backend_py', 'src', 'main.py'));

    if (pyDistReady) {
      const pyHandler = new lambda.Function(this, 'ApiPyHandler', {
        runtime: lambda.Runtime.PYTHON_3_12,
        handler: 'app.backend_py.src.main.handler',
        functionName: `specodex-api-py-${config.stage}`,
        memorySize: 512,
        timeout: cdk.Duration.seconds(30),
        environment: {
          STAGE: config.stage,
          APP_MODE: 'public',
          NODE_ENV: 'production',
          DYNAMODB_TABLE_NAME: table.tableName,
          UPLOAD_BUCKET: uploadBucket.bucketName,
          AWS_ACCOUNT_ID: config.env.account,
          SSM_PREFIX: config.ssmPrefix,
        },
        code: lambda.Code.fromAsset(pyDistPath, {
          exclude: ['**/__pycache__', '**/*.pyc', '**/*.pyo'],
        }),
      });

      table.grantReadWriteData(pyHandler);
      uploadBucket.grantReadWrite(pyHandler);
      pyHandler.addToRolePolicy(ssmPolicy);

      const pyIntegration = new apigwIntegrations.HttpLambdaIntegration(
        'ApiPyIntegration',
        pyHandler,
      );

      // /api/v2/* → Python Lambda. Frontend's VITE_API_VERSION=v2
      // flips requests to this path; v1 stays live alongside until
      // Phase 3 (Express deletion) per Nick's "full cutover, no
      // canary" answer recorded in todo/PYTHON_BACKEND.md §7.
      this.api.addRoutes({
        path: '/api/v2/{proxy+}',
        methods: [
          apigw.HttpMethod.GET,
          apigw.HttpMethod.HEAD,
          apigw.HttpMethod.POST,
          apigw.HttpMethod.PUT,
          apigw.HttpMethod.PATCH,
          apigw.HttpMethod.DELETE,
          apigw.HttpMethod.OPTIONS,
        ],
        integration: pyIntegration,
      });

      new cdk.CfnOutput(this, 'ApiV2Url', {
        value: `${this.api.url || ''}api/v2/`,
        description:
          'Python FastAPI base URL (Phase 1.3, behind VITE_API_VERSION=v2)',
      });
    } else {
      // Loud at synth time so a Nick running `cdk diff` sees the
      // skip and can decide whether to build first.
      // eslint-disable-next-line no-console
      console.warn(
        '[ApiStack] app/backend_py/dist/ missing — Python /api/v2 Lambda will NOT be deployed. ' +
          'Run `./Quickstart build-backend-py` then re-run cdk diff/deploy to include it.',
      );
    }

    new cdk.CfnOutput(this, 'ApiUrl', { value: this.api.url || '' });
  }
}
