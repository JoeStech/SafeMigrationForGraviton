#!/bin/bash
set -euo pipefail

echo "=== Creating DynamoDB tables ==="

# Sessions table
if aws dynamodb describe-table --table-name SafeMigration-Sessions >/dev/null 2>&1; then
  echo "Sessions table already exists, skipping."
else
  echo "Creating Sessions table..."
  aws dynamodb create-table \
    --table-name SafeMigration-Sessions \
    --attribute-definitions \
      AttributeName=sessionToken,AttributeType=S \
      AttributeName=userId,AttributeType=S \
    --key-schema AttributeName=sessionToken,KeyType=HASH \
    --global-secondary-indexes '[{
      "IndexName": "userId-index",
      "KeySchema": [{"AttributeName": "userId", "KeyType": "HASH"}],
      "Projection": {"ProjectionType": "ALL"}
    }]' \
    --billing-mode PAY_PER_REQUEST

  aws dynamodb wait table-exists --table-name SafeMigration-Sessions

  aws dynamodb update-time-to-live \
    --table-name SafeMigration-Sessions \
    --time-to-live-specification Enabled=true,AttributeName=ttl

  echo "Sessions table created with TTL enabled."
fi

# Jobs table
if aws dynamodb describe-table --table-name SafeMigration-Jobs >/dev/null 2>&1; then
  echo "Jobs table already exists, skipping."
else
  echo "Creating Jobs table..."
  aws dynamodb create-table \
    --table-name SafeMigration-Jobs \
    --attribute-definitions \
      AttributeName=jobId,AttributeType=S \
      AttributeName=userId,AttributeType=S \
      AttributeName=createdAt,AttributeType=N \
    --key-schema AttributeName=jobId,KeyType=HASH \
    --global-secondary-indexes '[{
      "IndexName": "userId-createdAt-index",
      "KeySchema": [
        {"AttributeName": "userId", "KeyType": "HASH"},
        {"AttributeName": "createdAt", "KeyType": "RANGE"}
      ],
      "Projection": {"ProjectionType": "ALL"}
    }]' \
    --billing-mode PAY_PER_REQUEST

  aws dynamodb wait table-exists --table-name SafeMigration-Jobs
  echo "Jobs table created."
fi

echo "=== DynamoDB tables ready ==="
