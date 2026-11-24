---
paths:
  - "app/infrastructure/**"
  - ".github/workflows/**"
  - "cli/quickstart.py"
  - "scripts/build_backend_py.sh"
  - "scripts/hooks/**"
  - "tests/post_deploy/**"
  - "tests/staging/**"
  - "tests/integration/test_deploy_readiness.py"
---

# Deploy chain, post-deploy verification, AWS auth

The one-line rules in `CLAUDE.md` and the global `~/.claude/rules/cloud-security.md` always apply. This loads when you touch CDK, workflows, the Quickstart gate, or the deploy tests. Deploy-chain edits ship as a DRAFT PR into `dev`; the `/cicd` skill (local-only, `.claude/skills/cicd/`) is the audit runbook.

## The chain

`.github/workflows/ci.yml`: `test-python`, `test-integration`, `test-backend`, `test-frontend`, `test-deploy-build`, `test-codegen` → `deploy-staging` (push to `dev` or `master`; `environment: staging`) → `smoke-staging` → `deploy-prod` (`master` only; `environment: production`, JimothyJohn the only required reviewer) → `smoke-prod`. PRs into either branch run tests only. `./Quickstart verify` mirrors the test jobs; its `deploy-build` stage is the AWS-free half of Deploy Staging (workspace install, public frontend build, Lambda bundle with a fresh lockfile resolve, Python bundle, `cdk synth` with a placeholder account).

**Green PR ≠ green `dev`: the deploy build is a fresh resolve.** Deploy Staging builds the backend Lambda bundle by writing a devDependencies-free manifest into `app/backend/dist/` (`cli/quickstart.py:lambda_bundle_manifest`) and running a fresh, non-workspace `npm install --package-lock-only` there, then `npx cdk deploy` via ts-node. Two things the workspace lockfile hides have broken it post-merge: a devDependency peer-range conflict (typescript 7 vs `@typescript-eslint`) and a `typescript` major that drops the JS compiler API ts-node needs — `app/infrastructure` runs ts-node against `@typescript/typescript6` (`cdk.json` sets `TS_NODE_COMPILER`), so that package must stay a JS-API compiler. Both fail at PR time via `tests/integration/test_deploy_readiness.py::TestLambdaBundleResolves` and `::TestInfrastructureBuild`; if you touch the bundle step or the infra toolchain, extend those tests rather than waiting for the `dev` run.

## Post-deploy verification

After `./Quickstart deploy --stage <stage>` returns, confirm the stack is live before closing the loop. `./Quickstart smoke <URL>` runs the full `tests/post_deploy/` suite; the ad-hoc checks below are for a single misbehaving endpoint.

**URLs per stage.** Prod is the custom domain; staging/dev come from the Frontend stack's `CloudFrontUrl` output:

    # staging / dev
    aws cloudformation describe-stacks \
      --stack-name DatasheetMiner-<Stage>-Frontend \
      --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontUrl`].OutputValue' --output text

    # prod
    https://www.specodex.com

**Canonical endpoints** — each must 200 with the shape noted:

| Endpoint                          | Expected (HTTP 200)                                                        |
|-----------------------------------|----------------------------------------------------------------------------|
| `/health`                         | `{"status": "healthy", "timestamp": "...", "environment": "production", "mode": "public"}` |
| `/api/products/categories`        | `{"success": true, "data": [{type, count, display_name}, ...]}`            |
| `/api/products/summary`           | `{"success": true, "data": {"total": N, ...}}`                              |
| `/api/products`                   | `{"success": true, "data": [...]}` (array, possibly empty)                  |
| `/api/v1/search?type=<valid>`     | `{"success": true, ...}` (400 if type not in the Zod enum)                  |

**One-shot smoke:**

    ./Quickstart smoke https://www.specodex.com          # prod
    ./Quickstart smoke "$(aws cloudformation describe-stacks \
      --stack-name DatasheetMiner-Staging-Frontend \
      --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontUrl`].OutputValue' \
      --output text)"                                       # staging

**Common failure fingerprints:**

- `/health` times out but `/api/products` 200s → the CloudFront behavior for `/health` is wrong (it should route to API Gateway like `/api/*`). Check `app/infrastructure/lib/frontend-stack.ts` behaviors.
- `/health` returns `"mode": "admin"` → the Lambda is running with the local dev env. `APP_MODE: 'public'` is hardcoded in `app/infrastructure/lib/api-stack.ts`; verify that code shipped.
- `/api/products/categories` returns `data: []` → the DynamoDB table is empty or the Lambda points at the wrong table. Check `DYNAMODB_TABLE_NAME` in the Lambda env.
- `/api/v1/search?type=<new>` returns 400 → the Zod enum derives from the generated `VALID_PRODUCT_TYPES`; `./Quickstart gen-types` wasn't run or its output wasn't deployed (`rules/product-types.md`).

## AWS auth paths — two of them, don't mix them up

The account has two GitHub-Actions auth principals with overlapping but **non-identical** permissions:

- **OIDC role `gh-deploy-datasheetminer`** — what `ci.yml` uses (`role-to-assume:` in `deploy-staging` and `deploy-prod`). Inline policy `CdkDeploy`. This is the one CI deploys with.
- **IAM user `datasheetminer-github`** — static-creds principal with managed policy `datasheetminer-cicd`. Not referenced by any current workflow, but its policy is broader and easy to mistake for "the deploy policy".

When adding a deploy permission, attach it to the **role's `CdkDeploy` inline policy** (or a managed policy attached to the role). Adding it only to `datasheetminer-cicd` does nothing for CI (the `Route53Lookup` perm for `HostedZone.fromLookup` was once added there and CI kept failing). Verify with:

    aws iam get-role-policy --role-name gh-deploy-datasheetminer --policy-name CdkDeploy \
      --query 'PolicyDocument.Statement[?Sid==`<your-sid>`]'

## Apex (2-part) domains and `HOSTED_ZONE_NAME`

`app/infrastructure/lib/config.ts` infers the hosted-zone name from `DOMAIN_NAME` when `HOSTED_ZONE_NAME` is unset: 3+ labels strip the leftmost (`app.example.com` → `example.com`); 2 labels use the domain itself (`specodex.com` → `specodex.com`, because the parent would be `com`, which `fromLookup` can't resolve — synth fails with `Found zones: [] for dns:com`). It uses `||`, not `??`, so an empty string from an unset Actions secret also falls through. `HOSTED_ZONE_NAME` is not required for apex deploys but works as an override when the zone differs from the inferred parent. The live host is `www.specodex.com`.

## Pushing workflow changes

`gh`'s default token has `repo` but not `workflow` scope, so a push touching `.github/workflows/*` is rejected even after auth succeeds. Either `gh auth refresh -s workflow` (interactive — hand it off as a `scratch/` script) or Nick pushes from his terminal.
