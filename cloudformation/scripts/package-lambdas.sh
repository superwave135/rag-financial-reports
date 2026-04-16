#!/usr/bin/env bash
# package-lambdas.sh — Re-package and re-upload Lambda functions without redeploying the stack.
#
# Use this when you've updated Lambda code but don't need to change infrastructure.
# After running this script, update the Lambda functions in AWS with:
#   aws lambda update-function-code --function-name <name> --s3-bucket <bucket> --s3-key lambdas/ingest.zip
#
# Or just re-run deploy.sh — CloudFormation will only update the Lambda functions.
#
# Usage:
#   cd rag-financial-reports
#   bash cloudformation/scripts/package-lambdas.sh [--region us-east-1]

set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-us-east-1}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --region) REGION="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
DEPLOY_BUCKET="rag-fin-deploy-${ACCOUNT_ID}-${REGION}"
DIST_DIR="${ROOT_DIR}/dist"

echo "Packaging Lambdas and uploading to s3://${DEPLOY_BUCKET}/lambdas/"
mkdir -p "${DIST_DIR}"

for LAMBDA in ingest query; do
    echo ""
    echo "--- ${LAMBDA} ---"
    LAMBDA_SRC="${ROOT_DIR}/lambdas/${LAMBDA}"
    PKG_DIR="${DIST_DIR}/${LAMBDA}_pkg"
    ZIP_FILE="${DIST_DIR}/${LAMBDA}.zip"

    rm -rf "${PKG_DIR}"
    mkdir -p "${PKG_DIR}"

    if [ -s "${LAMBDA_SRC}/requirements.txt" ]; then
        echo "  Installing dependencies..."
        pip install \
            -r "${LAMBDA_SRC}/requirements.txt" \
            -t "${PKG_DIR}" \
            --quiet \
            --upgrade
    fi

    cp "${LAMBDA_SRC}/handler.py" "${PKG_DIR}/"
    rm -f "${ZIP_FILE}"
    (cd "${PKG_DIR}" && zip -r "${ZIP_FILE}" . \
        --exclude "*.pyc" \
        --exclude "*/__pycache__/*" \
        > /dev/null)

    SIZE=$(du -sh "${ZIP_FILE}" | cut -f1)
    echo "  Packaged: ${ZIP_FILE} (${SIZE})"

    aws s3 cp "${ZIP_FILE}" "s3://${DEPLOY_BUCKET}/lambdas/${LAMBDA}.zip" --region "${REGION}"
    echo "  Uploaded to s3://${DEPLOY_BUCKET}/lambdas/${LAMBDA}.zip"
done

echo ""
echo "Done. To apply the updated code, run deploy.sh (CloudFormation will detect the change)."
