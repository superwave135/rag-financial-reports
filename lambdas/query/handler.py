"""
Query Lambda for Financial Reports RAG.

Called by API Gateway (POST /query). Accepts a natural-language financial question
with optional filters, retrieves relevant report excerpts from OpenSearch, then
generates a grounded answer using Claude.

Request body (JSON):
    {
        "question":      "What was Apple's total revenue in FY2024?",   (required)
        "ticker":        "AAPL",    (optional — scope to one company)
        "fiscal_period": "2024",    (optional — scope to one fiscal period)
        "top_k":         5          (optional — chunks to retrieve, default 5)
    }

Response body (JSON):
    {
        "answer": "Apple's total revenue for FY2024 was $391.0 billion...",
        "sources": [
            {
                "ticker": "AAPL",
                "report_type": "annual",
                "fiscal_period": "2024",
                "source": "reports/AAPL/annual/2024/AAPL-10K-2024.pdf",
                "chunk_index": 14
            }
        ]
    }
"""

import json
import os
import boto3
import requests
from requests_aws4auth import AWS4Auth

bedrock_embed = boto3.client("bedrock-runtime", region_name=os.environ.get("BEDROCK_REGION", "us-east-1"))
bedrock_gen   = boto3.client("bedrock-runtime", region_name=os.environ["AWS_REGION"])

OPENSEARCH_ENDPOINT = os.environ["OPENSEARCH_ENDPOINT"].rstrip("/")
INDEX_NAME          = os.environ.get("INDEX_NAME", "financial-reports")
REGION              = os.environ["AWS_REGION"]

EMBEDDING_MODEL  = "amazon.titan-embed-text-v2:0"
GENERATION_MODEL = os.environ.get("GENERATION_MODEL", "amazon.nova-pro-v1:0")
EMBEDDING_DIM    = 1024
DEFAULT_TOP_K    = 5

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
}


# ── Embedding ──────────────────────────────────────────────────────────────────

def embed_question(text: str) -> list[float]:
    """Embed the user's question into a 1024-dimensional normalized vector."""
    response = bedrock_embed.invoke_model(
        modelId=EMBEDDING_MODEL,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "inputText": text,
            "dimensions": EMBEDDING_DIM,
            "normalize": True,
        }),
    )
    return json.loads(response["body"].read())["embedding"]


# ── Retrieval ──────────────────────────────────────────────────────────────────

def retrieve_chunks(embedding: list[float], top_k: int,
                    ticker: str | None, fiscal_period: str | None) -> list[dict]:
    """
    k-NN vector search in OpenSearch with optional metadata filters.

    If ticker or fiscal_period are specified, the knn search is wrapped in a
    bool query with term filters so results are scoped to the requested company
    and/or reporting period.

    Returns a list of source dicts: text + ticker + report_type + fiscal_period + source.
    """
    # Build the knn query
    knn_clause = {
        "knn": {
            "embedding": {
                "vector": embedding,
                "k": top_k * 2,  # over-fetch so filters have enough candidates
            }
        }
    }

    # Wrap with filters if provided
    filters = []
    if ticker:
        filters.append({"term": {"ticker": ticker.upper()}})
    if fiscal_period:
        filters.append({"term": {"fiscal_period": fiscal_period}})

    query = (
        {"bool": {"must": [knn_clause], "filter": filters}}
        if filters
        else knn_clause
    )

    search_body = {
        "size": top_k,
        "_source": ["text", "ticker", "report_type", "fiscal_period", "source", "chunk_index"],
        "query": query,
    }

    url        = f"{OPENSEARCH_ENDPOINT}/{INDEX_NAME}/_search"
    body_bytes = json.dumps(search_body).encode()
    creds      = boto3.Session().get_credentials().get_frozen_credentials()
    awsauth    = AWS4Auth(creds.access_key, creds.secret_key, REGION, "aoss",
                          session_token=creds.token)
    resp = requests.post(url, data=body_bytes,
                         headers={"Content-Type": "application/json"},
                         auth=awsauth)

    if resp.status_code >= 400:
        raise RuntimeError(f"OpenSearch search failed [{resp.status_code}]: {resp.content[:500].decode()}")

    hits = resp.json().get("hits", {}).get("hits", [])
    return [hit["_source"] for hit in hits]


# ── Generation ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a financial analyst assistant specializing in corporate financial reports "
    "(10-K annual reports, 10-Q quarterly reports).\n\n"
    "Answer the user's question using ONLY the report excerpts provided below as context. "
    "Do not use outside knowledge or assumptions.\n\n"
    "Guidelines:\n"
    "- Cite specific figures with their units (e.g. USD millions, %) and time periods.\n"
    "- If multiple companies or periods appear in the context, distinguish between them clearly.\n"
    "- If the answer cannot be found in the context, say exactly: "
    "'This information is not available in the provided report excerpts.'\n"
    "- Keep the answer concise and factual."
)


def generate_answer(question: str, chunks: list[dict]) -> str:
    """
    Call the generation model with retrieved report excerpts as grounding context.
    Supports both Anthropic Claude (anthropic_version body) and Amazon Nova (messages/inferenceConfig body).
    Each chunk is labelled with its source company and period so the model can attribute figures correctly.
    """
    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        ticker  = chunk.get("ticker", "?")
        rtype   = chunk.get("report_type", "?").upper()
        period  = chunk.get("fiscal_period", "?")
        header  = f"[Excerpt {i} — {ticker} {rtype} FY{period}]"
        context_parts.append(f"{header}\n{chunk['text']}")

    context = "\n\n---\n\n".join(context_parts)
    prompt  = (
        f"{SYSTEM_PROMPT}\n\n"
        f"CONTEXT FROM FINANCIAL REPORTS:\n{context}\n\n"
        f"QUESTION:\n{question}\n\n"
        f"ANSWER:"
    )

    # Choose request body format based on model family
    if "anthropic" in GENERATION_MODEL:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }
    else:
        # Amazon Nova (and other non-Anthropic models)
        body = {
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"max_new_tokens": 1024},
        }

    response = bedrock_gen.invoke_model(
        modelId=GENERATION_MODEL,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(body),
    )
    result = json.loads(response["body"].read())

    # Extract text from whichever response format was returned
    if "content" in result:
        return result["content"][0]["text"]                          # Anthropic Claude
    return result["output"]["message"]["content"][0]["text"]         # Amazon Nova


# ── Entry point ────────────────────────────────────────────────────────────────

def handler(event, context):
    """Lambda entry point — invoked by API Gateway."""

    # Handle CORS preflight
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS_HEADERS, "body": ""}

    try:
        body          = json.loads(event.get("body") or "{}")
        question      = body.get("question", "").strip()
        ticker        = body.get("ticker", "").strip().upper() or None
        fiscal_period = body.get("fiscal_period", "").strip() or None
        top_k         = min(int(body.get("top_k", DEFAULT_TOP_K)), 20)  # cap at 20

        if not question:
            return {
                "statusCode": 400,
                "headers": CORS_HEADERS,
                "body": json.dumps({"error": "Missing required field: 'question'"}),
            }

        print(f"Question: {question!r} | ticker={ticker} | "
              f"fiscal_period={fiscal_period} | top_k={top_k}")

        # Step 1: embed the question
        embedding = embed_question(question)

        # Step 2: retrieve relevant chunks
        chunks = retrieve_chunks(embedding, top_k, ticker, fiscal_period)
        print(f"Retrieved {len(chunks)} chunks")

        if not chunks:
            hint = ""
            if ticker or fiscal_period:
                filters = " | ".join(filter(None, [ticker, fiscal_period]))
                hint = f" Make sure the report for [{filters}] has been uploaded and ingested."
            return {
                "statusCode": 200,
                "headers": CORS_HEADERS,
                "body": json.dumps({
                    "answer": f"No relevant content found.{hint}",
                    "sources": [],
                }),
            }

        # Step 3: generate grounded answer
        answer = generate_answer(question, chunks)
        print(f"Answer preview: {answer[:120]}...")

        # Deduplicate sources for the response metadata
        sources, seen = [], set()
        for chunk in chunks:
            key = (chunk.get("ticker"), chunk.get("report_type"), chunk.get("fiscal_period"))
            if key not in seen:
                seen.add(key)
                sources.append({
                    "ticker":        chunk.get("ticker"),
                    "report_type":   chunk.get("report_type"),
                    "fiscal_period": chunk.get("fiscal_period"),
                    "source":        chunk.get("source"),
                    "chunk_index":   chunk.get("chunk_index"),
                })

        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": json.dumps({"answer": answer, "sources": sources}),
        }

    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"Error: {exc}")
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": str(exc)}),
        }
