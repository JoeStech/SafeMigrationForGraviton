# SafeMigration

Automates Graviton (ARM/arm64) migration for GitHub-hosted CI/CD pipelines. Users authenticate via GitHub, select a repository, and the system forks it into a sandbox, analyzes pipeline files using Amazon Bedrock, generates arm64-compatible changes with automatic secrets/database stubbing, and opens a reviewable pull request.

## Architecture

- **Compute:** 10 AWS Lambda functions (Python 3.12)
- **Orchestration:** AWS Step Functions state machine
- **Storage:** DynamoDB (Sessions + Jobs tables)
- **AI:** Amazon Bedrock (Claude) for analysis and change generation
- **Auth:** GitHub App OAuth
- **Frontend:** React SPA (TypeScript + Vite)
- **Deployment:** AWS CLI bash scripts

## Project Structure

```
src/
├── models.py                        # Shared dataclasses, enums, constants
├── shared.py                        # Shared utilities (json_response, validate_session)
├── data/
│   ├── session_store.py             # DynamoDB session CRUD
│   └── job_store.py                 # DynamoDB job CRUD + token storage
├── lambdas/
│   ├── auth/handler.py              # GitHub OAuth flow
│   ├── repo/handler.py              # Repository listing + validation
│   ├── fork/handler.py              # Fork creation + migration branch
│   ├── analyze/handler.py           # File scanning + Bedrock analysis
│   ├── generate/handler.py          # arm64 change generation with diffs
│   ├── stub/handler.py              # External dependency detection + mocking
│   ├── pr/handler.py                # Commit + pull request creation
│   ├── check_pipeline/handler.py    # GitHub Actions status polling
│   ├── feedback/handler.py          # Failure capture + corrective loop
│   └── job/handler.py               # Job CRUD + Step Functions trigger
└── dashboard/                       # React SPA
infra/
├── deploy.sh                        # Main deployment orchestrator
├── deploy_tables.sh                 # DynamoDB table creation
├── deploy_lambdas.sh                # Lambda packaging + deployment
├── deploy_api.sh                    # API Gateway HTTP API + routes
├── deploy_stepfn.sh                 # Step Functions state machine
└── state_machine.json               # ASL definition
tests/                               # 28 tests (moto-based, no AWS needed)
```

## Prerequisites

1. **AWS CLI** configured with permissions for Lambda, DynamoDB, API Gateway, Step Functions, Bedrock
2. **Python 3.12** (Lambda runtime)
3. **Node.js 18+** (dashboard)
4. **GitHub App** registered at https://github.com/settings/apps with:
   - OAuth enabled (client ID + client secret)
   - Callback URL pointing to your dashboard
   - Permissions: `repo` (read/write), `read:user`

## IAM Roles

Create these before deploying (the scripts don't create IAM resources):

**Lambda Execution Role** — trust `lambda.amazonaws.com`, attach:
- `AWSLambdaBasicExecutionRole`
- Custom policy: `dynamodb:*` on `SafeMigration-*`, `states:StartExecution`, `bedrock:InvokeModel`

**Step Functions Role** — trust `states.amazonaws.com`, attach:
- Custom policy: `lambda:InvokeFunction` on `safemigration-*`

## Deployment

### Environment Variables

```bash
export LAMBDA_ROLE_ARN="arn:aws:iam::ACCOUNT:role/SafeMigrationLambdaRole"
export STEP_FUNCTIONS_ROLE_ARN="arn:aws:iam::ACCOUNT:role/SafeMigrationStepFunctionsRole"
export GITHUB_CLIENT_ID="your-github-app-client-id"
export GITHUB_CLIENT_SECRET="your-github-app-client-secret"

# Optional (have defaults)
export AWS_REGION="us-east-1"
export GITHUB_REDIRECT_URI="https://your-dashboard.com/callback"
export BEDROCK_MODEL_ID="anthropic.claude-3-sonnet-20240229-v1:0"
export DASHBOARD_ORIGIN="https://your-dashboard.com"
```

### Full Deployment

```bash
bash infra/deploy.sh
```

Runs four scripts in order:

1. **`deploy_tables.sh`** — Creates DynamoDB tables with GSIs and TTL. Idempotent.
2. **`deploy_lambdas.sh`** — Packages full `src/` tree + pip deps into a zip per Lambda. Creates/updates 10 functions with handler paths like `src.lambdas.auth.handler.handler`.
3. **`deploy_stepfn.sh`** — Substitutes Lambda ARN placeholders in `state_machine.json`, creates/updates the state machine. Prints `STATE_MACHINE_ARN`.
4. **`deploy_api.sh`** — Creates HTTP API Gateway, wires 10 routes, configures CORS. Prints the API URL.

After first deploy, export `STATE_MACHINE_ARN` and re-run `deploy_lambdas.sh` so the job Lambda can trigger executions.

### Dashboard

```bash
cd src/dashboard
npm install
echo "VITE_API_URL=https://YOUR_API_ID.execute-api.us-east-1.amazonaws.com" > .env
npm run build
# Upload dist/ to S3, CloudFront, or any static host
```

## Running Tests

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest tests/ -v
```

28 tests pass. Uses `moto` to mock DynamoDB — no AWS credentials needed.

## Token Security

GitHub access tokens are stored in the Jobs DynamoDB table at job creation time. Step Functions Lambdas fetch the token via `get_github_token(job_id)` instead of passing it through state machine execution history.

## Pipeline Stages

```
Fork → Analyze → Generate → Stub → PR → Wait → CheckPipeline → [Feedback ×3] → Complete
```

Each stage is a Lambda with explicit Step Functions `Parameters` mappings. After PR creation, `CheckPipeline` polls GitHub Actions. If the pipeline fails, the feedback loop runs up to 3 corrective cycles via Bedrock before escalating to manual review.

## API Routes

| Method | Path | Lambda | Description |
|--------|------|--------|-------------|
| GET | /auth/login | auth | Returns GitHub OAuth URL |
| POST | /auth/callback | auth | Exchanges code for session |
| POST | /auth/logout | auth | Invalidates session |
| GET | /auth/session | auth | Validates session token |
| GET | /repos | repo | Lists user's repositories |
| GET | /repos/{owner}/{repo}/validate | repo | Checks for migratable artifacts |
| POST | /jobs | job | Creates migration job |
| GET | /jobs | job | Lists user's jobs |
| GET | /jobs/{jobId} | job | Gets job details |
| GET | /jobs/{jobId}/status | job | Gets pipeline stage status |
