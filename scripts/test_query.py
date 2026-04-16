#!/usr/bin/env python3
"""
Test the Financial Reports RAG API from the command line.

Usage:
    # Ask a general question across all ingested reports
    python scripts/test_query.py \\
        --url https://xxxx.execute-api.us-east-1.amazonaws.com/prod/query \\
        --question "Which company had the highest revenue growth in 2024?"

    # Ask about a specific company
    python scripts/test_query.py \\
        --url https://xxxx.execute-api.us-east-1.amazonaws.com/prod/query \\
        --question "What was Apple's gross margin in FY2024?" \\
        --ticker AAPL

    # Narrow by both company and fiscal period
    python scripts/test_query.py \\
        --url https://xxxx.execute-api.us-east-1.amazonaws.com/prod/query \\
        --question "What were Nvidia's data center revenues?" \\
        --ticker NVDA \\
        --period 2024

    # Retrieve the URL from CloudFormation
    API_URL=$(aws cloudformation describe-stacks \\
        --stack-name rag-financial-reports-dev \\
        --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \\
        --output text)
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def query_api(api_url: str, question: str, ticker: str | None,
              fiscal_period: str | None, top_k: int) -> dict:
    payload = {"question": question, "top_k": top_k}
    if ticker:
        payload["ticker"] = ticker.upper()
    if fiscal_period:
        payload["fiscal_period"] = fiscal_period

    data    = json.dumps(payload).encode()
    request = urllib.request.Request(
        api_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        print(f"HTTP {exc.code} Error: {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"Connection error: {exc.reason}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Query the Financial Reports RAG API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--url", required=True,
        help="API Gateway URL (ApiUrl from CloudFormation outputs)",
    )
    parser.add_argument(
        "--question", "-q", required=True,
        help="Natural-language question about the financial reports",
    )
    parser.add_argument(
        "--ticker", "-t", metavar="TICKER",
        help="Scope to a specific company (e.g. AAPL, NVDA, AMZN, GOOGL)",
    )
    parser.add_argument(
        "--period", "-p", metavar="PERIOD",
        help="Scope to a fiscal period (e.g. 2024, 2024Q3)",
    )
    parser.add_argument(
        "--top-k", type=int, default=5,
        help="Number of report chunks to retrieve (default: 5)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Print raw JSON response instead of formatted output",
    )
    args = parser.parse_args()

    # Print query info
    print()
    print("=" * 60)
    print(f"  Question : {args.question}")
    if args.ticker:
        print(f"  Ticker   : {args.ticker.upper()}")
    if args.period:
        print(f"  Period   : {args.period}")
    print(f"  Top-K    : {args.top_k}")
    print("=" * 60)
    print("  Querying API...", end="", flush=True)

    t0     = time.time()
    result = query_api(args.url, args.question, args.ticker, args.period, args.top_k)
    elapsed = time.time() - t0
    print(f" done in {elapsed:.1f}s")

    if args.json:
        print(json.dumps(result, indent=2))
        return

    # Formatted output
    print()
    print("ANSWER")
    print("-" * 60)
    print(result.get("answer", "(no answer)"))

    sources = result.get("sources", [])
    if sources:
        print()
        print(f"SOURCES  ({len(sources)} report(s) cited)")
        print("-" * 60)
        for s in sources:
            ticker  = s.get("ticker", "?")
            rtype   = s.get("report_type", "?").upper()
            period  = s.get("fiscal_period", "?")
            src     = s.get("source", "")
            print(f"  {ticker:6s} | {rtype:10s} | FY{period:8s} | {src}")

    if "error" in result:
        print()
        print(f"ERROR: {result['error']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
