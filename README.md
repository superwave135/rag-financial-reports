# RAG Financial Reports

Serverless Retrieval-Augmented Generation (RAG) system on AWS for querying listed company financial reports (Apple, Nvidia, Amazon, Google, etc.) using natural language.

Upload annual reports (10-K) or quarterly reports (10-Q) to S3 in any supported format. The system automatically ingests, chunks, and embeds them into a vector store. Query via REST API.

**Supported file formats:** `.pdf`, `.docx`, `.xlsx`, `.csv`, `.html` / `.htm`, `.txt`

###### Git push from local to remote
git remote set-url origin git@github.com:superwave135/rag-financial-reports.git
git push -u origin main

## Architecture

```
  INGESTION (automatic on S3 upload)
  ────────────────────────────────────────────────────────────────
  S3 (reports/AAPL/annual/2024/...)
      │  S3 ObjectCreated event
      ▼
  Ingest Lambda
      ├─ format dispatcher → extract text (pdf/docx/xlsx/csv/html/txt)
      ├─ chunk (1000 chars, 200 overlap)
      ├─ Bedrock Titan Embed v2 → 1024-dim vector
      └─ OpenSearch Serverless → store {text, embedding, ticker, report_type, fiscal_period}

  QUERY (on demand via API)
  ────────────────────────────────────────────────────────────────
  POST /query  {"question": "...", "ticker": "AAPL", "fiscal_period": "2024"}
      │
      ▼
  API Gateway → Query Lambda
      ├─ Bedrock Titan Embed v2 → embed question
      ├─ OpenSearch Serverless k-NN search (optional company/period filters)
      ├─ Bedrock Claude 3.5 Haiku → grounded answer from retrieved excerpts
      └─ Response: {answer, sources[]}
```

## AWS Services

| Service | Purpose |
|---|---|
| S3 | Report storage (`reports/{TICKER}/{type}/{period}/file.{ext}`) |
| Lambda (Ingest) | File → extract text → chunks → embeddings → OpenSearch |
| Lambda (Query) | Question → embed → kNN → Claude → answer |
| Bedrock Titan Embed v2 | Text embeddings (1024 dimensions) |
| Bedrock Claude 3.5 Haiku | Financial Q&A generation |
| OpenSearch Serverless | Vector store + metadata-filtered search |
| API Gateway | REST endpoint (`POST /query`) |
| IAM | Least-privilege roles for Lambda |
| CloudFormation | Infrastructure as Code |

## Prerequisites

1. **AWS CLI** configured with credentials that have permission to create IAM roles, Lambda, S3, OpenSearch Serverless, and API Gateway.

2. **Python 3.x** with `pip` and `boto3` installed locally (for the helper scripts).

3. **Bedrock model access** — the following models are used and must be accessible in your account:
   - **Amazon Titan Embed Text v2** (`amazon.titan-embed-text-v2:0`)
   - **Anthropic Claude 3.5 Haiku** (`anthropic.claude-haiku-4-5-20251001-v1:0`)

   As of 2025, AWS automatically enables Bedrock foundation models on first invocation — no manual activation needed. However, **first-time Anthropic model users** may be prompted to submit use case details before access is granted. If your queries return a Bedrock access error, check the Model catalog in the AWS Console and complete any required steps for the Anthropic model.

## Deployment

```bash
# Clone and enter the project
git clone <your-repo-url>
cd rag-financial-reports

# Deploy to AWS (creates all resources)
bash cloudformation/scripts/deploy.sh

# Optional: specify region and environment
bash cloudformation/scripts/deploy.sh --region ap-southeast-1 --env dev
```

The deploy script:
1. Creates a temporary deployment S3 bucket (`rag-fin-deploy-{account}-{region}`)
2. Packages both Lambda functions with their dependencies
3. Uploads the zips to the deployment bucket
4. Runs `aws cloudformation deploy` to provision all resources

The stack takes **3–5 minutes** to deploy, mostly waiting for OpenSearch Serverless to provision.

After deployment, the script prints a table with:
- `ReportsBucketName` — upload your PDFs here
- `ApiUrl` — POST your questions here

## Upload Financial Reports

Files must follow this S3 key structure:

```
reports/{TICKER}/{report_type}/{fiscal_period}/{filename}.{ext}
```

| Segment | Values | Example |
|---|---|---|
| `TICKER` | Company ticker (uppercase) | `AAPL`, `NVDA`, `AMZN`, `GOOGL` |
| `report_type` | `annual`, `quarterly`, `other` | `annual` |
| `fiscal_period` | Year or year+quarter | `2024`, `2024Q3` |
| `filename` | Any descriptive filename | `AAPL-10K-2024.pdf` |
| `ext` | Supported format extension | `.pdf`, `.docx`, `.xlsx`, `.csv`, `.html`, `.htm`, `.txt` |

### Using the upload script

```bash
# Single file (PDF)
python scripts/upload_reports.py \
    --bucket fin-reports-rag-123456789012 \
    --file ./sample-reports/AAPL-10K-2024.pdf \
    --ticker AAPL \
    --type annual \
    --period 2024

# Single file (HTML — e.g. SEC EDGAR filing)
python scripts/upload_reports.py \
    --bucket fin-reports-rag-123456789012 \
    --file ./sample-reports/GOOGL-10K-2024.html \
    --ticker GOOGL \
    --type annual \
    --period 2024

# Bulk upload from CSV
python scripts/upload_reports.py \
    --bucket fin-reports-rag-123456789012 \
    --batch reports.csv
```

CSV format (`reports.csv`):
```
./sample-reports/AAPL-10K-2024.pdf,AAPL,annual,2024
./sample-reports/NVDA-10K-2024.pdf,NVDA,annual,2024
./sample-reports/AMZN-10K-2024.pdf,AMZN,annual,2024
./sample-reports/GOOGL-10K-2024.html,GOOGL,annual,2024
```

### Upload files using AWS CLI directly

```bash
aws s3 cp sample-reports/AMZN-10K-2025.pdf s3://fin-reports-rag-881786084229/reports/AMZN/annual/2025/AMZN-10K-2025.pdf --region ap-southeast-1

```
###### To watch the ingest Lambda logs in real time after uploading a PDF:
The Ingest Lambda triggers automatically within seconds of each upload. Check Lambda logs in CloudWatch for progress. Large reports (100+ pages) take 2–5 minutes to index.

###### Command to watch Lambda logs ingestion in real-time
aws logs tail /aws/lambda/fin-reports-rag-ingest-dev --follow --region ap-southeast-1


## Query the API

### Using the test script

```bash
# Get the API URL from CloudFormation outputs
API_URL=$(aws cloudformation describe-stacks \
    --stack-name rag-financial-reports-dev \
    --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
    --output text)

# Ask a question
python scripts/test_query.py \
    --url $API_URL \
    --question "What was Apple's total revenue in FY2024?"

# Filter by company
python scripts/test_query.py \
    --url $API_URL \
    --question "What were the main risk factors?" \
    --ticker NVDA

# Filter by company and period
python scripts/test_query.py \
    --url $API_URL \
    --question "What was net income?" \
    --ticker AAPL \
    --period 2024
```

###### Command Query
(.venv) geekytan@geeky:~/Documents/dev/rag-financial-reports$ python3 scripts/test_query.py \
    --url "https://e8iqnnzxvf.execute-api.ap-southeast-1.amazonaws.com/prod/query" \
    --question "What was Apple's EPS in FY2025?"

============================================================
  Question : What was Apple's EPS in FY2025?
  Top-K    : 5
============================================================
  Querying API... done in 1.4s

ANSWER
------------------------------------------------------------
Apple's basic earnings per share (EPS) in FY2025 was $7.49, and the diluted EPS was $7.46.

SOURCES  (1 report(s) cited)
------------------------------------------------------------
  AAPL   | ANNUAL     | FY2025     | reports/AAPL/annual/2025/AAPL-10K-2025.pdf

### Query using curl

```bash
curl -X POST $API_URL \
    -H "Content-Type: application/json" \
    -d '{
        "question": "What was Apple revenue in FY2024?",
        "ticker": "AAPL",
        "fiscal_period": "2024"
    }'
```

(.venv) geekytan@geeky:~/Documents/dev/rag-financial-reports$ 
###### Command URL
curl -s -X POST "https://e8iqnnzxvf.execute-api.ap-southeast-1.amazonaws.com/prod/query" -H "Content-Type: application/json" -d '{"question": "What was Nvidia total revenue in FY2025?", "ticker": "NVDA", "fiscal_period": "2025"}' | python3 -m json.tool

curl -s -X POST "https://e8iqnnzxvf.execute-api.ap-southeast-1.amazonaws.com/prod/query" -H "Content-Type: application/json" -d '{"question": "What was key driver of growth for Amazon in FY2025?", "ticker": "AMZN", "fiscal_period": "2025"}' | python3 -m json.tool

###### Response Answer
{
    "answer": "In FY2025, Nvidia's total revenue was $130,497 million.",
    "sources": [
        {
            "ticker": "NVDA",
            "report_type": "annual",
            "fiscal_period": "2025",
            "source": "reports/NVDA/annual/2025/NVDA-10K-2025.pdf",
            "chunk_index": 415
        }
    ]
}



### Request / Response format

**Request:**
```json
{
    "question":      "What was Apple's gross margin in 2024?",
    "ticker":        "AAPL",      // optional — filter to one company
    "fiscal_period": "2024",      // optional — filter to one period
    "top_k":         5            // optional — chunks to retrieve (default 5)
}
```

**Response:**
```json
{
    "answer": "Apple's gross margin for fiscal year 2024 was 46.2%...",
    "sources": [
        {
            "ticker":        "AAPL",
            "report_type":   "annual",
            "fiscal_period": "2024",
            "source":        "reports/AAPL/annual/2024/AAPL-10K-2024.pdf",
            "chunk_index":   47
        }
    ]
}
```

## Cost Estimate

| Service | Approximate cost |
|---|---|
| OpenSearch Serverless | ~$0.24/hour (~$175/month) when active |
| Lambda | Near zero for occasional queries |
| Bedrock Titan Embed | ~$0.02 per 1M tokens |
| Bedrock Claude 3.5 Haiku | ~$0.80/$4.00 per 1M input/output tokens |
| S3 | Negligible |

**OpenSearch Serverless is the dominant cost.** If you are not using the system continuously, tear down the stack after use.

## Teardown

```bash
bash cloudformation/scripts/destroy.sh

# Or with options
bash cloudformation/scripts/destroy.sh --region ap-southeast-1 --env dev
```

This deletes everything: the CloudFormation stack, all S3 buckets (including uploaded PDFs), the OpenSearch collection, and both Lambda functions.

## Updating Lambda Code

If you modify the Lambda handler code without changing infrastructure:

```bash
# Re-package and re-upload Lambda zips
bash cloudformation/scripts/package-lambdas.sh

# Re-deploy (CloudFormation detects the code change)
bash cloudformation/scripts/deploy.sh
```

## Project Structure

```
rag-financial-reports/
├── cloudformation/
│   ├── template.yaml              # Main CloudFormation stack (all AWS resources)
│   ├── parameters/
│   │   ├── dev.json               # Dev environment parameter overrides
│   │   └── prod.json              # Prod environment parameter overrides
│   └── scripts/
│       ├── deploy.sh              # Build + deploy everything
│       ├── destroy.sh             # Tear down everything
│       └── package-lambdas.sh    # Re-package Lambdas without full redeploy
├── lambdas/
│   ├── ingest/
│   │   ├── handler.py             # S3-triggered ingestion function (multi-format)
│   │   └── requirements.txt       # pypdf, python-docx, openpyxl, beautifulsoup4, urllib3
│   └── query/
│       ├── handler.py             # API Gateway query function
│       └── requirements.txt       # urllib3
├── scripts/
│   ├── upload_reports.py          # CLI tool to upload PDFs to S3
│   └── test_query.py              # CLI tool to test queries against the API
└── sample-reports/
    └── .gitkeep                   # Put your local PDFs here (not committed to git)
```

## Supported Report Sources

Financial reports can be downloaded from:
- **Apple**: [investor.apple.com](https://investor.apple.com/sec-filings/default.aspx)
- **Nvidia**: [investor.nvidia.com](https://investor.nvidia.com/financial-info/sec-filings/default.aspx)
- **Amazon**: [ir.aboutamazon.com](https://ir.aboutamazon.com/annual-reports-proxies-and-shareholder-letters/default.aspx)
- **Google/Alphabet**: [abc.xyz/investor](https://abc.xyz/investor/)
- **SEC EDGAR**: [sec.gov/cgi-bin/browse-edgar](https://www.sec.gov/cgi-bin/browse-edgar) (all public companies)

> **Tip:** SEC EDGAR serves most filings as HTML (`.htm`) in addition to PDF. HTML filings are often more text-complete than PDFs and work well with the `.html`/`.htm` extractor.
