#!/bin/bash
set -euo pipefail

# Deploy the analyze Lambda as a container image (includes Arm MCP server).
# All other Lambdas continue to use zip packaging via deploy_lambdas.sh.

export AWS_DEFAULT_REGION=us-east-1

LAMBDA_ROLE_ARN="${LAMBDA_ROLE_ARN:?Set LAMBDA_ROLE_ARN env var}"
GITHUB_CLIENT_ID="${GITHUB_CLIENT_ID:?Set GITHUB_CLIENT_ID env var}"
GITHUB_CLIENT_SECRET="${GITHUB_CLIENT_SECRET:?Set GITHUB_CLIENT_SECRET env var}"
GITHUB_REDIRECT_URI="${GITHUB_REDIRECT_URI:-http://localhost:3000/callback}"
BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-global.anthropic.claude-sonnet-4-6}"
STATE_MACHINE_ARN="${STATE_MACHINE_ARN:-}"
GITHUB_APP_ID="${GITHUB_APP_ID:-}"
GITHUB_APP_PRIVATE_KEY_FILE="${GITHUB_APP_PRIVATE_KEY_FILE:-safemigration.2026-03-12.private-key.pem}"

FUNC_NAME="safemigration-analyze"
ECR_REPO_NAME="safemigration-analyze"
TIMEOUT=300
MEMORY=2048  # MCP server + sentence-transformers needs more RAM

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_DEFAULT_REGION}.amazonaws.com/${ECR_REPO_NAME}"

echo "=== Deploying analyze Lambda (container image with Arm MCP) ==="

# 1. Create ECR repo if needed
aws ecr describe-repositories --repository-names "$ECR_REPO_NAME" >/dev/null 2>&1 || \
  aws ecr create-repository --repository-name "$ECR_REPO_NAME" --image-scanning-configuration scanOnPush=true --no-cli-pager

# 2. Build the container image
echo "Building analyze Lambda image..."
aws ecr get-login-password | docker login --username AWS --password-stdin \
  "${ACCOUNT_ID}.dkr.ecr.${AWS_DEFAULT_REGION}.amazonaws.com"

BUILD_ARGS=""
if [ -f "$PROJECT_ROOT/$GITHUB_APP_PRIVATE_KEY_FILE" ]; then
  BUILD_ARGS="--build-arg PEM_FILE=$GITHUB_APP_PRIVATE_KEY_FILE"
fi

docker build \
  --platform linux/arm64 \
  --provenance=false \
  -f "$SCRIPT_DIR/analyze-lambda/Dockerfile" \
  $BUILD_ARGS \
  -t "$ECR_REPO_NAME:latest" \
  "$PROJECT_ROOT"

docker tag "$ECR_REPO_NAME:latest" "$ECR_URI:latest"
docker push "$ECR_URI:latest"

IMAGE_URI="$ECR_URI:latest"
echo "Image pushed: $IMAGE_URI"

# 3. Build environment variables
ENV_VARS="Variables={SESSIONS_TABLE=SafeMigration-Sessions,JOBS_TABLE=SafeMigration-Jobs"
ENV_VARS+=",GITHUB_CLIENT_ID=$GITHUB_CLIENT_ID,GITHUB_CLIENT_SECRET=$GITHUB_CLIENT_SECRET"
ENV_VARS+=",GITHUB_REDIRECT_URI=$GITHUB_REDIRECT_URI,BEDROCK_MODEL_ID=$BEDROCK_MODEL_ID"
ENV_VARS+=",MCP_SERVER_DIR=/app,MCP_VENV_PYTHON=/app/.venv/bin/python"
ENV_VARS+=",MCP_SERVER_SCRIPT=/app/mcp_server_wrapper.py"
ENV_VARS+=",TOKENIZERS_PARALLELISM=false,DISABLE_MLFLOW_INTEGRATION=TRUE"
if [ -n "$STATE_MACHINE_ARN" ]; then
  ENV_VARS+=",STATE_MACHINE_ARN=$STATE_MACHINE_ARN"
fi
if [ -n "$GITHUB_APP_ID" ]; then
  ENV_VARS+=",GITHUB_APP_ID=$GITHUB_APP_ID"
fi
# For container image Lambdas, the PEM is baked into the image at /var/task/
if [ -f "$GITHUB_APP_PRIVATE_KEY_FILE" ]; then
  PEM_BASENAME=$(basename "$GITHUB_APP_PRIVATE_KEY_FILE")
  ENV_VARS+=",GITHUB_APP_PRIVATE_KEY_PATH=/var/task/$PEM_BASENAME"
fi
ENV_VARS+="}"

# 4. Create or update the Lambda function
if aws lambda get-function --function-name "$FUNC_NAME" >/dev/null 2>&1; then
  echo "Updating existing Lambda..."
  aws lambda update-function-code \
    --function-name "$FUNC_NAME" \
    --image-uri "$IMAGE_URI" \
    --no-cli-pager
  aws lambda wait function-updated --function-name "$FUNC_NAME" 2>/dev/null || sleep 10
  aws lambda update-function-configuration \
    --function-name "$FUNC_NAME" \
    --timeout $TIMEOUT \
    --memory-size $MEMORY \
    --environment "$ENV_VARS" \
    --no-cli-pager
else
  echo "Creating new Lambda..."
  aws lambda create-function \
    --function-name "$FUNC_NAME" \
    --package-type Image \
    --code "ImageUri=$IMAGE_URI" \
    --role "$LAMBDA_ROLE_ARN" \
    --timeout $TIMEOUT \
    --memory-size $MEMORY \
    --environment "$ENV_VARS" \
    --no-cli-pager
fi

echo "=== $FUNC_NAME deployed (container image) ==="
