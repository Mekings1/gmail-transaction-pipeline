#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
STATE_BUCKET="gmail-txn-tf-state-${ACCOUNT_ID}"
LOCK_TABLE="gmail-txn-tf-locks"

echo "▶ Creating Terraform state bucket: ${STATE_BUCKET}"
aws s3api create-bucket \
  --bucket "${STATE_BUCKET}" \
  --region "${AWS_REGION}" \
  --create-bucket-configuration LocationConstraint="${AWS_REGION}"

aws s3api put-bucket-versioning \
  --bucket "${STATE_BUCKET}" \
  --versioning-configuration Status=Enabled

aws s3api put-public-access-block \
  --bucket "${STATE_BUCKET}" \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

echo "▶ Creating Terraform lock table: ${LOCK_TABLE}"
aws dynamodb create-table \
  --table-name "${LOCK_TABLE}" \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region "${AWS_REGION}" > /dev/null

echo ""
echo "✓ Done. Your backend values:"
echo "  bucket         = \"${STATE_BUCKET}\""
echo "  dynamodb_table = \"${LOCK_TABLE}\""
echo "  region         = \"${AWS_REGION}\""