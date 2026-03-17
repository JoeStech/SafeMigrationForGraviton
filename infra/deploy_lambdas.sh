#!/bin/bash
set -euo pipefail

# All infrastructure lives in us-east-1
export AWS_DEFAULT_REGION=us-east-1

LAMBDA_ROLE_ARN="${LAMBDA_ROLE_ARN:?Set LAMBDA_ROLE_ARN env var}"
GITHUB_CLIENT_ID="${GITHUB_CLIENT_ID:?Set GITHUB_CLIENT_ID env var}"
GITHUB_CLIENT_SECRET="${GITHUB_CLIENT_SECRET:?Set GITHUB_CLIENT_SECRET env var}"
GITHUB_REDIRECT_URI="${GITHUB_REDIRECT_URI:-http://localhost:3000/callback}"
BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-anthropic.claude-3-sonnet-20240229-v1:0}"
STATE_MACHINE_ARN="${STATE_MACHINE_ARN:-}"
GITHUB_APP_ID="${GITHUB_APP_ID:-}"
GITHUB_APP_PRIVATE_KEY_FILE="${GITHUB_APP_PRIVATE_KEY_FILE:-safemigration.2026-03-12.private-key.pem}"

# analyze is deployed separately as a container image (includes Arm MCP server)
# — see deploy_analyze_lambda.sh
LAMBDAS=(auth repo fork generate stub pr feedback job check_pipeline complete)
RUNTIME="python3.12"
MEMORY=256

echo "=== Deploying Lambda functions ==="

BUILD_DIR=$(mktemp -d)
trap "rm -rf $BUILD_DIR" EXIT

echo "Installing dependencies for Lambda (linux arm64)..."
# Use runtime-only requirements (no test deps like hypothesis/pytest/moto)
LAMBDA_REQS="requirements-lambda.txt"
if [ ! -f "$LAMBDA_REQS" ]; then
  echo "WARNING: $LAMBDA_REQS not found, falling back to requirements.txt"
  LAMBDA_REQS="requirements.txt"
fi

.venv/bin/pip install -t "$BUILD_DIR" -r "$LAMBDA_REQS" -q \
  --platform manylinux2014_aarch64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  --no-deps 2>/dev/null \
  || true

# Second pass: install with deps to catch anything missed
.venv/bin/pip install -t "$BUILD_DIR" -r "$LAMBDA_REQS" -q \
  --platform manylinux2014_aarch64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: 2>/dev/null \
  || python3 -m pip install -t "$BUILD_DIR" -r "$LAMBDA_REQS" -q \
  --platform manylinux2014_aarch64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all:

# Verify no macOS .so files leaked in
if find "$BUILD_DIR" -name "*.so" -exec file {} \; 2>/dev/null | grep -q "Mach-O"; then
  echo "ERROR: macOS native binaries found in build dir. Platform install failed."
  exit 1
fi

echo "Packaging shared source tree..."
SHARED_ZIP="/tmp/safemigration-src.zip"
rm -f "$SHARED_ZIP"

# Zip the entire src/ tree (excluding dashboard) so imports like
# "from src.data.job_store import ..." resolve correctly.
zip -r "$SHARED_ZIP" src/ -x "src/dashboard/*" -q

# Bundle the GitHub App private key file if it exists
if [ -f "$GITHUB_APP_PRIVATE_KEY_FILE" ]; then
  echo "Bundling GitHub App private key: $GITHUB_APP_PRIVATE_KEY_FILE"
  zip -j "$SHARED_ZIP" "$GITHUB_APP_PRIVATE_KEY_FILE" -q
fi

# Add pip dependencies into the same zip
cd "$BUILD_DIR" && zip -r "$SHARED_ZIP" . -q && cd - >/dev/null

for fn in "${LAMBDAS[@]}"; do
  FUNC_NAME="safemigration-${fn//_/-}"
  ZIP_FILE="/tmp/${fn}.zip"
  cp "$SHARED_ZIP" "$ZIP_FILE"

  # Timeout varies by Lambda
  if [ "$fn" = "check_pipeline" ]; then
    TIMEOUT=360
  elif [ "$fn" = "generate" ] || [ "$fn" = "feedback" ] || [ "$fn" = "stub" ]; then
    TIMEOUT=120
  else
    TIMEOUT=30
  fi

  HANDLER="src.lambdas.${fn}.handler.handler"

  ENV_VARS="Variables={SESSIONS_TABLE=SafeMigration-Sessions,JOBS_TABLE=SafeMigration-Jobs"
  ENV_VARS+=",GITHUB_CLIENT_ID=$GITHUB_CLIENT_ID,GITHUB_CLIENT_SECRET=$GITHUB_CLIENT_SECRET"
  ENV_VARS+=",GITHUB_REDIRECT_URI=$GITHUB_REDIRECT_URI,BEDROCK_MODEL_ID=$BEDROCK_MODEL_ID"
  if [ -n "$STATE_MACHINE_ARN" ]; then
    ENV_VARS+=",STATE_MACHINE_ARN=$STATE_MACHINE_ARN"
  fi
  if [ -n "$GITHUB_APP_ID" ]; then
    ENV_VARS+=",GITHUB_APP_ID=$GITHUB_APP_ID"
  fi
  if [ -f "$GITHUB_APP_PRIVATE_KEY_FILE" ]; then
    # Key is bundled in the zip at the root level — Lambda extracts to /var/task/
    PEM_BASENAME=$(basename "$GITHUB_APP_PRIVATE_KEY_FILE")
    ENV_VARS+=",GITHUB_APP_PRIVATE_KEY_PATH=/var/task/$PEM_BASENAME"
  fi
  ENV_VARS+="}"

  echo "Deploying $FUNC_NAME (handler=$HANDLER, timeout=${TIMEOUT}s)..."

  if aws lambda get-function --function-name "$FUNC_NAME" >/dev/null 2>&1; then
    aws lambda update-function-code \
      --function-name "$FUNC_NAME" \
      --zip-file "fileb://$ZIP_FILE" \
      --architectures arm64 \
      --no-cli-pager
    aws lambda wait function-updated --function-name "$FUNC_NAME" 2>/dev/null || sleep 5
    aws lambda update-function-configuration \
      --function-name "$FUNC_NAME" \
      --handler "$HANDLER" \
      --environment "$ENV_VARS" \
      --timeout $TIMEOUT \
      --memory-size $MEMORY \
      --no-cli-pager
  else
    aws lambda create-function \
      --function-name "$FUNC_NAME" \
      --runtime "$RUNTIME" \
      --handler "$HANDLER" \
      --zip-file "fileb://$ZIP_FILE" \
      --role "$LAMBDA_ROLE_ARN" \
      --timeout $TIMEOUT \
      --memory-size $MEMORY \
      --architectures arm64 \
      --environment "$ENV_VARS" \
      --no-cli-pager
  fi

  echo "  $FUNC_NAME deployed."
done

echo "=== All Lambda functions deployed ==="
