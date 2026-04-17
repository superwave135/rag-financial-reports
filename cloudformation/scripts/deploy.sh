#!/usr/bin/env bash
# deploy.sh — Build, package, and deploy the Financial Reports RAG stack to AWS.
#
# Usage:
#   cd rag-financial-reports
#   bash cloudformation/scripts/deploy.sh [--region us-east-1] [--env dev]
#
# Prerequisites:
#   - AWS CLI configured (aws configure)
#   - Python 3.x with pip
#   - Bedrock models enabled (see README — Titan Embed v2 in us-east-1, Nova Pro via APAC inference profile)

set -euo pipefail

# ── Parse args ─────────────────────────────────────────────────────────────────
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
ENV=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --region) REGION="$2"; shift 2 ;;
        --env)    ENV="$2";    shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# ── Prompt for environment if not provided ─────────────────────────────────────
if [ -z "${ENV}" ]; then
    echo "Select deployment environment:"
    echo "  1) dev"
    echo "  2) prod"
    read -r -p "Enter choice [1/2]: " ENV_CHOICE
    case "${ENV_CHOICE}" in
        1) ENV="dev" ;;
        2) ENV="prod" ;;
        *) echo "Invalid choice. Aborting."; exit 1 ;;
    esac
fi

if [[ "${ENV}" != "dev" && "${ENV}" != "prod" ]]; then
    echo "Invalid environment '${ENV}'. Must be 'dev' or 'prod'. Aborting."
    exit 1
fi

echo ""
read -r -p "Deploy to '${ENV}' environment in region '${REGION}'? Type 'yes' to confirm: " CONFIRM
if [ "${CONFIRM}" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

# ── Derived config ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
STACK_NAME="rag-financial-reports-${ENV}"
COLLECTION_NAME="fin-reports-rag-${ENV}"
DEPLOY_BUCKET="rag-fin-deploy-${ACCOUNT_ID}-${REGION}"
DIST_DIR="${ROOT_DIR}/dist"

echo "============================================================"
echo "  RAG Financial Reports — Deploy"
echo "  Environment : ${ENV}"
echo "  Region      : ${REGION}"
echo "  Account     : ${ACCOUNT_ID}"
echo "  Stack       : ${STACK_NAME}"
echo "  Deploy S3   : s3://${DEPLOY_BUCKET}"
echo "============================================================"
echo ""

# ── Step 1: Deployment bucket ──────────────────────────────────────────────────
echo "[1/5] Checking deployment bucket..."
if aws s3api head-bucket --bucket "${DEPLOY_BUCKET}" --region "${REGION}" 2>/dev/null; then
    echo "  Bucket exists: s3://${DEPLOY_BUCKET}"
else
    echo "  Creating deployment bucket: s3://${DEPLOY_BUCKET}"
    if [ "${REGION}" = "us-east-1" ]; then
        aws s3api create-bucket --bucket "${DEPLOY_BUCKET}" --region "${REGION}"
    else
        aws s3api create-bucket \
            --bucket "${DEPLOY_BUCKET}" \
            --region "${REGION}" \
            --create-bucket-configuration LocationConstraint="${REGION}"
    fi
    echo "  Created."
fi

# ── Step 2: Package Lambda functions ──────────────────────────────────────────
echo ""
echo "[2/5] Packaging Lambda functions..."
mkdir -p "${DIST_DIR}"

for LAMBDA in ingest query; do
    echo "  > ${LAMBDA}"
    LAMBDA_SRC="${ROOT_DIR}/lambdas/${LAMBDA}"
    PKG_DIR="${DIST_DIR}/${LAMBDA}_pkg"
    ZIP_FILE="${DIST_DIR}/${LAMBDA}.zip"

    rm -rf "${PKG_DIR}"
    mkdir -p "${PKG_DIR}"

    # Install dependencies
    if [ -s "${LAMBDA_SRC}/requirements.txt" ]; then
        pip install \
            -r "${LAMBDA_SRC}/requirements.txt" \
            -t "${PKG_DIR}" \
            --quiet \
            --upgrade
    fi

    # Copy handler
    cp "${LAMBDA_SRC}/handler.py" "${PKG_DIR}/"

    # Zip (exclude compiled bytecode)
    rm -f "${ZIP_FILE}"
    (cd "${PKG_DIR}" && zip -r "${ZIP_FILE}" . \
        --exclude "*.pyc" \
        --exclude "*/__pycache__/*" \
        > /dev/null)

    SIZE=$(du -sh "${ZIP_FILE}" | cut -f1)
    echo "    Packaged ${LAMBDA}.zip (${SIZE})"
done

# ── Step 3: Upload Lambda zips to S3 ──────────────────────────────────────────
echo ""
echo "[3/5] Uploading Lambda packages to S3..."
aws s3 cp "${DIST_DIR}/ingest.zip" "s3://${DEPLOY_BUCKET}/lambdas/ingest.zip" --region "${REGION}"
aws s3 cp "${DIST_DIR}/query.zip"  "s3://${DEPLOY_BUCKET}/lambdas/query.zip"  --region "${REGION}"
echo "  Uploaded."

# ── Step 4: Deploy CloudFormation stack ───────────────────────────────────────
echo ""
echo "[4/5] Deploying CloudFormation stack: ${STACK_NAME}..."
echo "  (This takes ~3-5 minutes for OpenSearch Serverless to provision)"
aws cloudformation deploy \
    --template-file "${ROOT_DIR}/cloudformation/template.yaml" \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides \
        Environment="${ENV}" \
        CollectionName="${COLLECTION_NAME}" \
        LambdaDeploymentBucket="${DEPLOY_BUCKET}" \
        IngestLambdaS3Key="lambdas/ingest.zip" \
        QueryLambdaS3Key="lambdas/query.zip" \
    --no-fail-on-empty-changeset

echo "  Stack deployed."

# ── Step 5: Show outputs ───────────────────────────────────────────────────────
echo ""
echo "[5/5] Stack outputs:"
echo "------------------------------------------------------------"
aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --query "Stacks[0].Outputs[*].{Key:OutputKey,Value:OutputValue}" \
    --output table

echo ""
echo "============================================================"
echo "  Done! Next steps:"
echo "  1. Upload reports : python scripts/upload_reports.py --help"
echo "  2. Test queries   : python scripts/test_query.py --help"
echo "============================================================"
