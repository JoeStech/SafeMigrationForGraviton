#!/bin/bash
set -euo pipefail

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION="${AWS_REGION:-us-east-1}"
DASHBOARD_ORIGIN="${DASHBOARD_ORIGIN:-http://localhost:3000}"

echo "=== Deploying API Gateway ==="

# Check if API already exists
API_ID=$(aws apigatewayv2 get-apis --query "Items[?Name=='SafeMigration'].ApiId" --output text 2>/dev/null || true)

if [ -z "$API_ID" ] || [ "$API_ID" = "None" ]; then
  echo "Creating HTTP API..."
  API_ID=$(aws apigatewayv2 create-api \
    --name SafeMigration \
    --protocol-type HTTP \
    --cors-configuration "AllowOrigins=$DASHBOARD_ORIGIN,AllowMethods=GET,POST,OPTIONS,AllowHeaders=Content-Type,Authorization" \
    --query ApiId --output text)
  echo "API created: $API_ID"
else
  echo "API already exists: $API_ID"
fi

# Create default stage with auto-deploy
aws apigatewayv2 create-stage \
  --api-id "$API_ID" \
  --stage-name '$default' \
  --auto-deploy 2>/dev/null || true

# Route definitions: METHOD PATH LAMBDA_NAME
ROUTES=(
  "GET /auth/login safemigration-auth"
  "POST /auth/callback safemigration-auth"
  "POST /auth/logout safemigration-auth"
  "GET /auth/session safemigration-auth"
  "GET /repos safemigration-repo"
  "GET /repos/{owner}/{repo}/validate safemigration-repo"
  "POST /jobs safemigration-job"
  "GET /jobs safemigration-job"
  "GET /jobs/{jobId} safemigration-job"
  "GET /jobs/{jobId}/status safemigration-job"
)

for route_def in "${ROUTES[@]}"; do
  read -r METHOD ROUTE_PATH FUNC_NAME <<< "$route_def"
  ROUTE_KEY="$METHOD $ROUTE_PATH"

  LAMBDA_ARN="arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${FUNC_NAME}"
  INTEGRATION_URI="arn:aws:apigateway:${REGION}:lambda:path/2015-03-31/functions/${LAMBDA_ARN}/invocations"

  # Create integration
  INTEGRATION_ID=$(aws apigatewayv2 create-integration \
    --api-id "$API_ID" \
    --integration-type AWS_PROXY \
    --integration-uri "$INTEGRATION_URI" \
    --payload-format-version "2.0" \
    --query IntegrationId --output text 2>/dev/null || true)

  if [ -n "$INTEGRATION_ID" ] && [ "$INTEGRATION_ID" != "None" ]; then
    # Create route
    aws apigatewayv2 create-route \
      --api-id "$API_ID" \
      --route-key "$ROUTE_KEY" \
      --target "integrations/$INTEGRATION_ID" \
      --no-cli-pager 2>/dev/null || true

    # Grant API Gateway permission to invoke Lambda
    aws lambda add-permission \
      --function-name "$FUNC_NAME" \
      --statement-id "apigateway-${METHOD}-${ROUTE_PATH//\//-}" \
      --action lambda:InvokeFunction \
      --principal apigateway.amazonaws.com \
      --source-arn "arn:aws:execute-api:${REGION}:${ACCOUNT_ID}:${API_ID}/*/${ROUTE_KEY}" \
      2>/dev/null || true

    echo "  Route: $ROUTE_KEY → $FUNC_NAME"
  fi
done

API_URL=$(aws apigatewayv2 get-api --api-id "$API_ID" --query ApiEndpoint --output text)
echo "=== API Gateway deployed: $API_URL ==="
