#!/bin/bash
set -euo pipefail

# Required env vars:
#   LAMBDA_ROLE_ARN, STEP_FUNCTIONS_ROLE_ARN
#   GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET
# Optional:
#   AWS_REGION (default: us-east-1)
#   GITHUB_REDIRECT_URI, BEDROCK_MODEL_ID, DASHBOARD_ORIGIN

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "============================================"
echo "  SafeMigration — Full Deployment"
echo "============================================"
echo ""

# 1. DynamoDB tables
echo "--- Step 1/5: DynamoDB Tables ---"
bash "$SCRIPT_DIR/deploy_tables.sh"
echo ""

# 2. Lambda functions (first pass without STATE_MACHINE_ARN)
echo "--- Step 2/5: Lambda Functions (zip) ---"
bash "$SCRIPT_DIR/deploy_lambdas.sh"
echo ""

# 2b. Analyze Lambda (container image with Arm MCP server)
echo "--- Step 2b/5: Analyze Lambda (container image) ---"
bash "$SCRIPT_DIR/deploy_analyze_lambda.sh"
echo ""

# 3. Step Functions (needs Lambda ARNs)
echo "--- Step 3/5: Step Functions ---"
bash "$SCRIPT_DIR/deploy_stepfn.sh"
echo ""

# 4. API Gateway
echo "--- Step 4/5: API Gateway ---"
bash "$SCRIPT_DIR/deploy_api.sh"
echo ""

echo "============================================"
echo "  Deployment complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Export the STATE_MACHINE_ARN printed above"
echo "  2. Re-run: bash infra/deploy_lambdas.sh  (to set STATE_MACHINE_ARN on job Lambda)"
echo "  3. Note the API Gateway URL from above"
echo "  4. Set VITE_API_URL in dashboard .env"
echo "  5. Update GITHUB_REDIRECT_URI to match"
echo "  6. Build and deploy the dashboard"
