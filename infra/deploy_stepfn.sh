#!/bin/bash
set -euo pipefail

STEP_FUNCTIONS_ROLE_ARN="${STEP_FUNCTIONS_ROLE_ARN:?Set STEP_FUNCTIONS_ROLE_ARN env var}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION="${AWS_REGION:-us-east-1}"
SM_NAME="SafeMigration-Orchestrator"

echo "=== Deploying Step Functions state machine ==="

# Replace Lambda ARN placeholders in the ASL definition
DEFINITION=$(cat infra/state_machine.json)
LAMBDAS=(Fork Analyze Generate Stub PR Feedback CheckPipeline Complete)
FUNC_NAMES=(fork analyze generate stub pr feedback check-pipeline complete)

for i in "${!LAMBDAS[@]}"; do
  PLACEHOLDER="\${${LAMBDAS[$i]}LambdaArn}"
  ARN="arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:safemigration-${FUNC_NAMES[$i]}"
  DEFINITION=$(echo "$DEFINITION" | sed "s|${PLACEHOLDER}|${ARN}|g")
done

# Check if state machine exists
SM_ARN=$(aws stepfunctions list-state-machines \
  --query "stateMachines[?name=='$SM_NAME'].stateMachineArn" \
  --output text 2>/dev/null || true)

if [ -z "$SM_ARN" ] || [ "$SM_ARN" = "None" ]; then
  echo "Creating state machine..."
  SM_ARN=$(aws stepfunctions create-state-machine \
    --name "$SM_NAME" \
    --definition "$DEFINITION" \
    --role-arn "$STEP_FUNCTIONS_ROLE_ARN" \
    --query stateMachineArn --output text)
  echo "State machine created: $SM_ARN"
else
  echo "Updating state machine..."
  aws stepfunctions update-state-machine \
    --state-machine-arn "$SM_ARN" \
    --definition "$DEFINITION" \
    --role-arn "$STEP_FUNCTIONS_ROLE_ARN" \
    --no-cli-pager
  echo "State machine updated: $SM_ARN"
fi

echo "=== Step Functions deployed: $SM_ARN ==="
echo "Export this for Lambda deployment:"
echo "  export STATE_MACHINE_ARN=$SM_ARN"
