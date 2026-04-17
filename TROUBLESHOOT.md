# Troubleshooting Log — RAG Financial Reports on AWS

A running record of every issue encountered during initial deployment and how each was resolved.

---

## 1. OpenSearch 403 Forbidden on Every Request

**Symptom**
Ingest Lambda returned `403 Forbidden` from OpenSearch Serverless on all index and document operations.

**Root Cause**
The CloudFormation data access policy principal was set to the Lambda IAM role ARN
(`arn:aws:iam::{AccountId}:role/fin-reports-rag-lambda-dev`), but the actual identity
Lambda presents at runtime is an *assumed-role* session ARN:
`arn:aws:sts::{AccountId}:assumed-role/fin-reports-rag-lambda-dev/fin-reports-rag-ingest-dev`.
OpenSearch Serverless does exact-string matching on the principal — the two forms don't match.
88XXXXXXXXX9
**Fix**
Changed the data access policy principal to the account root:
```
arn:aws:iam::{AccountId}:root
```
The account root covers all principals in the account, so both Lambda functions are
automatically authorised regardless of their session ARN.

**Debug Tip**
Add `sts.get_caller_identity()` to the Lambda handler temporarily to print the exact ARN
the function is running as — compare it against what's in the data access policy.

---

## 2. Lambda Package Uploaded to Wrong Region Bucket

**Symptom**
`package-lambdas.sh` failed or uploaded to a bucket that didn't exist.

**Root Cause**
The script defaulted to `us-east-1` and tried to upload to
`rag-fin-deploy-{AccountId}-us-east-1`, which was never created.
The stack was deployed to `ap-southeast-1`.

**Fix**
Always pass `--region ap-southeast-1` to the deploy script, or upload the zip manually
to the reports bucket that already exists:
```bash
aws s3 cp dist/query.zip s3://fin-reports-rag-{AccountId}/lambdas/query.zip --region ap-southeast-1
```

---

## 3. Titan Embed v2 Not Available in ap-southeast-1

**Symptom**
```
ValidationException: The provided model identifier is invalid
```
on `amazon.titan-embed-text-v2:0` from the Lambda running in `ap-southeast-1`.

**Root Cause**
`amazon.titan-embed-text-v2:0` is only available on-demand in `us-east-1`.
There is no APAC cross-region inference profile for Titan Embed — trying
`apac.amazon.titan-embed-text-v2:0` also fails.

**Fix**
Created a separate Bedrock client for embeddings that always targets `us-east-1`,
controlled by a `BEDROCK_REGION` environment variable:
```python
bedrock_embed = boto3.client("bedrock-runtime", region_name=os.environ.get("BEDROCK_REGION", "us-east-1"))
```
Both Lambda functions have `BEDROCK_REGION=us-east-1` in their CloudFormation env vars.
The generation client continues to use `AWS_REGION` (ap-southeast-1).

---

## 4. OpenSearch Serverless Does Not Support Caller-Specified Document IDs

**Symptom**
```
OpenSearch PUT failed [409]: Document ID is not supported in create/index operation request
```

**Root Cause**
OpenSearch Serverless VECTORSEARCH collections reject `PUT /{index}/_doc/{id}` — the
caller cannot specify a document ID.

**Fix**
Added `opensearch_post()` alongside `opensearch_put()` and changed `store_chunk()` to
use `POST /{index}/_doc` (OpenSearch auto-generates the ID):
```python
opensearch_post(f"/{INDEX_NAME}/_doc", doc)
```
The `doc_id` field (MD5 of the S3 key) is still stored as a body field for reference.

---

## 5. All Anthropic Claude Models Blocked by AWS Marketplace

**Symptom**
```
AccessDeniedException: not authorized to perform: aws-marketplace:ViewSubscriptions
```
on every Anthropic model: `anthropic.claude-3-haiku-20240307-v1:0`,
`global.anthropic.claude-haiku-4-5-20251001-v1:0`,
`apac.anthropic.claude-3-haiku-20240307-v1:0`.

**Root Cause**
Anthropic models on AWS Bedrock require an AWS Marketplace subscription record.
Even after adding `aws-marketplace:ViewSubscriptions` and `aws-marketplace:Subscribe`
IAM permissions, the error persists because the actual Marketplace subscription
(an account-level entitlement) doesn't exist — IAM permissions alone are not sufficient.

**Workaround**
Switched the generation model to **Amazon Nova Pro** (`apac.amazon.nova-pro-v1:0`),
which is Amazon's own model and requires no Marketplace subscription.

**To Resolve Properly (if you want Claude)**
Go to AWS Console → Amazon Bedrock → Model catalog → find any Claude model →
click "Subscribe" to create the Marketplace entitlement. This is a one-time account setup.

---

## 6. Amazon Nova Pro — Wrong Model ID Format

**Symptom**
```
ValidationException: Invocation of model ID amazon.nova-pro-v1:0 with on-demand throughput
isn't supported. Retry your request with the ID or ARN of an inference profile.
```

**Root Cause**
`amazon.nova-pro-v1:0` is the base foundation model ID. In `ap-southeast-1`, Nova Pro
is only accessible via a cross-region **inference profile**, not direct on-demand invocation.

**Fix**
Listed inference profiles to find the correct ID:
```bash
aws bedrock list-inference-profiles --region ap-southeast-1 \
  --query 'inferenceProfileSummaries[?contains(inferenceProfileName, `Nova Pro`)].{name:inferenceProfileName,id:inferenceProfileId}' \
  --output table
```
Result: `apac.amazon.nova-pro-v1:0` (APAC Nova Pro).
Also tried `ap.amazon.nova-pro-v1:0` — invalid. The correct prefix is `apac.`.

---

## 7. IAM Policy Missing Permission for Nova Pro Inference Profile

**Symptom**
```
AccessDeniedException: not authorized to perform: bedrock:InvokeModel on resource:
arn:aws:bedrock:ap-southeast-1:{AccountId}:inference-profile/apac.amazon.nova-pro-v1:0
```

**Root Cause**
The CloudFormation IAM policy allowed the foundation model ARN
(`arn:aws:bedrock:*::foundation-model/amazon.nova-pro-v1:0`) but not the inference
profile ARN. Inference profiles have a different ARN format that includes the account ID
and a different resource type (`inference-profile` not `foundation-model`).

**Fix**
Added the inference profile ARN to the IAM policy:
```yaml
- "arn:aws:bedrock:*:*:inference-profile/apac.amazon.nova-pro-v1:0"
```
Redeployed the CloudFormation stack to apply the IAM change.

**Key Learning**
When Bedrock access is denied, check the error message — it includes the exact resource ARN
that was attempted. Use that ARN directly in the IAM policy rather than guessing the format.

---

## 8. Terminal Bracketed Paste Mode Corruption

**Symptom**
Pasted commands were prefixed with `[200~` or `~`, causing errors like:
```
~curl: command not found
bash: [200~aws: command not found
```

**Root Cause**
The terminal had bracketed paste mode enabled, which wraps pasted text in escape sequences
(`\e[?2004h` / `\e[?2004l`). The shell was interpreting these literally.

**Fix**
Disable bracketed paste mode for the session by typing (not pasting) this command:
```bash
printf '\e[?2004l'
```
Alternatively, run multi-line commands as a single line to avoid the issue.

---

## Summary Table

| # | Issue | Root Cause | Fix |
|---|-------|-----------|-----|
| 1 | OpenSearch 403 | Principal ARN mismatch (role vs assumed-role) | Use account root as principal |
| 2 | Wrong S3 bucket region | Deploy script defaulted to us-east-1 | Pass `--region ap-southeast-1` |
| 3 | Titan Embed unavailable in ap-southeast-1 | No APAC inference profile for Titan Embed | Route embed calls to us-east-1 via `BEDROCK_REGION` env var |
| 4 | OpenSearch rejects document IDs | VECTORSEARCH collections don't support caller-specified IDs | Switch from PUT with ID to POST without ID |
| 5 | Anthropic Claude blocked | No AWS Marketplace subscription on account | Switched to Amazon Nova Pro |
| 6 | Nova Pro model ID invalid | Base model ID requires inference profile in ap-southeast-1 | Use `apac.amazon.nova-pro-v1:0` (found via `list-inference-profiles`) |
| 7 | Nova Pro IAM denied | IAM policy had wrong ARN format for inference profile | Add `arn:aws:bedrock:*:*:inference-profile/apac.amazon.nova-pro-v1:0` to policy |
| 8 | Paste corruption in terminal | Bracketed paste mode enabled | `printf '\e[?2004l'` to disable |
