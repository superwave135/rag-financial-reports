#!/usr/bin/env python3
"""
Upload financial report PDFs to S3 with the correct key structure.

The S3 key structure is:
    reports/{TICKER}/{report_type}/{fiscal_period}/{filename}

Examples:
    reports/AAPL/annual/2024/AAPL-10K-2024.pdf
    reports/NVDA/annual/2024/NVDA-10K-2024.pdf
    reports/AMZN/quarterly/2024Q3/AMZN-10Q-2024Q3.pdf
    reports/GOOGL/annual/2024/GOOGL-10K-2024.pdf

Usage:
    # Upload a single file
    python scripts/upload_reports.py \\
        --bucket fin-reports-rag-123456789012 \\
        --file ./sample-reports/AAPL-10K-2024.pdf \\
        --ticker AAPL \\
        --type annual \\
        --period 2024

    # Upload multiple files interactively
    python scripts/upload_reports.py \\
        --bucket fin-reports-rag-123456789012 \\
        --batch reports.csv

    # reports.csv format: filepath,ticker,type,period
    # ./sample-reports/AAPL-10K-2024.pdf,AAPL,annual,2024
    # ./sample-reports/NVDA-10K-2024.pdf,NVDA,annual,2024
"""

import argparse
import csv
import os
import sys

import boto3
from botocore.exceptions import ClientError


def get_s3_key(ticker: str, report_type: str, period: str, filename: str) -> str:
    return f"reports/{ticker.upper()}/{report_type.lower()}/{period}/{filename}"


def upload_file(s3_client, bucket: str, local_path: str,
                ticker: str, report_type: str, period: str) -> str:
    if not os.path.isfile(local_path):
        raise FileNotFoundError(f"File not found: {local_path}")

    filename = os.path.basename(local_path)
    s3_key   = get_s3_key(ticker, report_type, period, filename)
    size_mb  = os.path.getsize(local_path) / (1024 * 1024)

    print(f"  Uploading {filename} ({size_mb:.1f} MB)")
    print(f"    → s3://{bucket}/{s3_key}")

    s3_client.upload_file(local_path, bucket, s3_key)
    print(f"    Done. Ingest Lambda will trigger automatically.")
    return s3_key


def upload_batch(s3_client, bucket: str, csv_path: str) -> None:
    """Upload multiple files from a CSV manifest."""
    if not os.path.isfile(csv_path):
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f, fieldnames=["filepath", "ticker", "type", "period"])
        rows   = [row for row in reader if not row["filepath"].startswith("#")]

    print(f"Uploading {len(rows)} files from {csv_path}")
    ok = failed = 0
    for row in rows:
        try:
            upload_file(s3_client, bucket, row["filepath"].strip(),
                        row["ticker"].strip(), row["type"].strip(), row["period"].strip())
            ok += 1
        except Exception as exc:
            print(f"  ERROR: {exc}")
            failed += 1

    print(f"\nDone: {ok} uploaded, {failed} failed.")


def main():
    parser = argparse.ArgumentParser(
        description="Upload financial report PDFs to S3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--bucket", required=True,
        help="S3 bucket name (ReportsBucketName from CloudFormation outputs)",
    )
    parser.add_argument(
        "--file", metavar="PATH",
        help="Path to a single PDF file",
    )
    parser.add_argument(
        "--ticker", metavar="TICKER",
        help="Company ticker symbol (e.g. AAPL, NVDA, AMZN, GOOGL)",
    )
    parser.add_argument(
        "--type", dest="report_type", default="annual",
        choices=["annual", "quarterly", "other"],
        help="Report type (default: annual)",
    )
    parser.add_argument(
        "--period", metavar="PERIOD",
        help="Fiscal period (e.g. 2024 for annual, 2024Q3 for quarterly)",
    )
    parser.add_argument(
        "--batch", metavar="CSV",
        help="CSV file for bulk upload (columns: filepath,ticker,type,period)",
    )
    parser.add_argument(
        "--region", default=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        help="AWS region (default: us-east-1 or AWS_DEFAULT_REGION env var)",
    )
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name=args.region)

    # Verify bucket exists
    try:
        s3.head_bucket(Bucket=args.bucket)
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "404":
            print(f"Error: Bucket '{args.bucket}' does not exist.")
            print("  Run deploy.sh first, then copy the ReportsBucketName from the outputs.")
        else:
            print(f"Error accessing bucket '{args.bucket}': {exc}")
        sys.exit(1)

    if args.batch:
        upload_batch(s3, args.bucket, args.batch)
    elif args.file:
        if not args.ticker:
            parser.error("--ticker is required with --file")
        if not args.period:
            parser.error("--period is required with --file (e.g. 2024 or 2024Q3)")
        upload_file(s3, args.bucket, args.file, args.ticker, args.report_type, args.period)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
