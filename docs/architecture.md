# Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    RAG FINANCIAL REPORTS — AWS Architecture                 │
└─────────────────────────────────────────────────────────────────────────────┘

  INGESTION PIPELINE (triggered by S3 upload)
  ─────────────────────────────────────────────────────────────────────────────

  User/Script                S3 Bucket
  uploads PDF  ──────────►  reports/
                              AAPL/annual/2024/AAPL-10K-2024.pdf
                              NVDA/annual/2024/NVDA-10K-2024.pdf
                              AMZN/quarterly/2024Q3/AMZN-10Q-2024Q3.pdf
                              GOOGL/annual/2024/GOOGL-10K-2024.pdf
                                  │
                                  │ S3 ObjectCreated event
                                  │ (prefix filter: reports/, suffix: .pdf)
                                  ▼
                         ┌────────────────────────────────────┐
                         │          Ingest Lambda             │
                         │  Python 3.11 / 1024 MB / 600s      │
                         │                                    │
                         │  1. Parse S3 key → metadata        │
                         │     ticker, report_type,           │
                         │     fiscal_period                  │
                         │                                    │
                         │  2. pypdf → extract full text      │
                         │                                    │
                         │  3. Chunk text                     │
                         │     1000 chars, 200-char overlap   │
                         │                                    │
                         │  4. Titan Embed v2 per chunk       │
                         │     → 1024-dim float vector        │
                         │                                    │
                         │  5. Store in OpenSearch            │
                         └────────┬───────────────────────────┘
                                  │                │
                     ─────────────┘                └─────────────────
                     ▼                                               ▼
          ┌──────────────────────┐               ┌──────────────────────────────┐
          │   Amazon Bedrock     │               │  OpenSearch Serverless       │
          │                      │               │  (VECTORSEARCH collection)   │
          │  amazon.titan-       │               │                              │
          │  embed-text-v2:0     │               │  Index: financial-reports    │
          │                      │               │  ┌──────────────────────┐    │
          │  Input:  text chunk  │               │  │ embedding  knn_vector│    │
          │  Output: float[1024] │               │  │ text       text      │    │
          │  (normalized)        │               │  │ ticker     keyword   │    │
          └──────────────────────┘               │  │ report_type keyword  │    │
                                                 │  │ fiscal_period keyword│    │
                                                 │  │ source     keyword   │    │
                                                 │  │ chunk_index integer  │    │
                                                 │  │ doc_id     keyword   │    │
                                                 │  └──────────────────────┘    │
                                                 └──────────────────────────────┘

  QUERY PIPELINE (on demand via REST API)
  ─────────────────────────────────────────────────────────────────────────────

  Client                     API Gateway
  POST /query  ──────────►  REST API (prod stage)
  {                              │
    "question":                  │ Lambda Proxy Integration
    "What was AAPL               │
     revenue in 2024?",          ▼
    "ticker": "AAPL",   ┌────────────────────────────────────┐
    "fiscal_period":    │          Query Lambda              │
    "2024"              │  Python 3.11 / 512 MB / 30s        │
  }                     │                                    │
                        │  1. Parse request + optional       │
                        │     filters (ticker, period)       │
                        │                                    │
                        │  2. Titan Embed v2                 │
                        │     → embed question               │
                        │                                    │
                        │  3. OpenSearch k-NN search         │
                        │     top-5 chunks                   │
                        │     + optional metadata filter     │
                        │                                    │
                        │  4. Build prompt with excerpts     │
                        │     (each labelled by source)      │
                        │                                    │
                        │  5. Claude 3.5 Haiku               │
                        │     → grounded answer              │
                        └────────┬───────────────────────────┘
                                 │                │
                    ─────────────┘                └──────────────────
                    ▼                                                ▼
         ┌──────────────────────┐               ┌──────────────────────────────┐
         │   Amazon Bedrock     │               │  OpenSearch Serverless       │
         │                      │               │                              │
         │  (1) Titan Embed v2  │               │  k-NN search query           │
         │  → embed question    │               │  + bool filter:              │
         │                      │               │    {ticker: "AAPL"}          │
         │  (2) Claude 3.5 Haiku│               │    {fiscal_period: "2024"}   │
         │  → generate answer   │               │                              │
         │  max_tokens: 1024    │               │  Returns top-5 chunks        │
         └──────────────────────┘               │  with metadata               │
                                                └──────────────────────────────┘
                                 │
                                 ▼
                        {
                          "answer": "Apple's total net sales for
                                     fiscal 2024 were $391.0 billion...",
                          "sources": [
                            {
                              "ticker": "AAPL",
                              "report_type": "annual",
                              "fiscal_period": "2024",
                              "source": "reports/AAPL/annual/2024/...",
                              "chunk_index": 47
                            }
                          ]
                        }
```

## Resource Dependency Graph (CloudFormation)

```
EncryptionPolicy ─┐
NetworkPolicy    ─┤─► VectorCollection ─┐
                                        ├─► LambdaRole ─► IngestLambda ─► S3InvokeLambdaPermission ─► ReportsBucket


DataAccessPolicy ─────────────────────────────────────────────── (depends on LambdaRole.Arn)

LambdaRole ─► QueryLambda ─► ApiGatewayPermission
                          └─► RestApi ─► QueryResource ─► QueryMethod ─► ApiDeployment ─► ApiStage
                                                       └─► QueryOptionsMethod ─┘
```

## S3 Key Convention

```
reports/
├── AAPL/
│   ├── annual/
│   │   ├── 2023/
│   │   │   └── AAPL-10K-2023.pdf
│   │   └── 2024/
│   │       └── AAPL-10K-2024.pdf
│   └── quarterly/
│       ├── 2024Q1/
│       │   └── AAPL-10Q-2024Q1.pdf
│       └── 2024Q3/
│           └── AAPL-10Q-2024Q3.pdf
├── NVDA/
│   └── annual/
│       └── 2024/
│           └── NVDA-10K-2024.pdf
├── AMZN/
│   └── annual/
│       └── 2024/
│           └── AMZN-10K-2024.pdf
└── GOOGL/
    └── annual/
        └── 2024/
            └── GOOGL-10K-2024.pdf
```

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Embedding model | Titan Embed v2 (1024-dim) | Better quality than v1, normalized vectors, cheaper |
| Chunk size | 1000 chars, 200 overlap | Financial text is dense; larger chunks preserve table context |
| LLM | Amazon Nova Pro | Good financial reasoning, fast, cost-effective |
| Metadata filtering | Optional ticker + period filters | Scopes search to prevent cross-company confusion |
| Index creation | Lambda creates on first run | Ensures correct knn_vector mapping before any inserts |
| IaC | CloudFormation only (no CDK) | Single YAML file, no Node.js toolchain needed |
| Lambda deployment | S3-based packages | Supports full Python dependencies (pypdf) |
| OpenSearch access | Public endpoint + SigV4 | Simplest setup; SigV4 ensures only IAM-authorized callers |
