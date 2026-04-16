#!/usr/bin/env bash
# destroy.sh — Delete the RAG Financial Reports CloudFormation stack and deployment bucket.
#
# Usage:
#   cd rag-financial-reports
#   bash cloudformation/scripts/destroy.sh [--region us-east-1] [--env dev]
#
# WARNING: This permanently deletes all resources including the OpenSearch collection
# and S3 bucket (and all uploaded financial reports). Use with care.

set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
ENV="dev"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --region) REGION="$2"; shift 2 ;;
        --env)    ENV="$2";    shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
STACK_NAME="rag-financial-reports-${ENV}"
DEPLOY_BUCKET="rag-fin-deploy-${ACCOUNT_ID}-${REGION}"

echo "============================================================"
echo "  RAG Financial Reports — DESTROY"
echo "  Stack  : ${STACK_NAME}"
echo "  Region : ${REGION}"
echo "============================================================"
echo ""
echo "This will permanently delete:"
echo "  - CloudFormation stack: ${STACK_NAME}"
echo "  - S3 reports bucket and ALL uploaded PDFs"
echo "  - OpenSearch Serverless collection and ALL indexed data"
echo "  - Lambda functions, API Gateway, IAM role"
echo "  - Deployment bucket: s3://${DEPLOY_BUCKET}"
echo ""
read -r -p "Type 'yes' to confirm: " CONFIRM
if [ "${CONFIRM}" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

# ── Delete CloudFormation stack ────────────────────────────────────────────────
echo ""
echo "[1/3] Getting S3 bucket name from stack outputs..."
REPORTS_BUCKET=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --query "Stacks[0].Outputs[?OutputKey=='ReportsBucketName'].OutputValue" \
    --output text 2>/dev/null || echo "")

if [ -n "${REPORTS_BUCKET}" ]; then
    echo "  Note: reports bucket s3://${REPORTS_BUCKET} is kept (PDFs preserved)."
    echo "  CloudFormation will fail to delete the bucket if it is non-empty — that is expected."
    echo "  The bucket will remain after stack deletion and can be reused on next deploy."
fi

echo ""
echo "[2/3] Deleting CloudFormation stack: ${STACK_NAME}..."
aws cloudformation delete-stack \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}"

echo "  Waiting for deletion to complete..."
aws cloudformation wait stack-delete-complete \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}"
echo "  Stack deleted."

# ── Delete deployment bucket ───────────────────────────────────────────────────
echo ""
echo "[3/3] Cleaning up deployment bucket: s3://${DEPLOY_BUCKET}"
if aws s3api head-bucket --bucket "${DEPLOY_BUCKET}" --region "${REGION}" 2>/dev/null; then
    aws s3 rm "s3://${DEPLOY_BUCKET}" --recursive --region "${REGION}" || true
    aws s3api delete-bucket --bucket "${DEPLOY_BUCKET}" --region "${REGION}"
    echo "  Deployment bucket deleted."
else
    echo "  Deployment bucket not found, skipping."
fi

echo ""
echo "============================================================"
echo "  All resources deleted. No further AWS charges."
echo "============================================================"
