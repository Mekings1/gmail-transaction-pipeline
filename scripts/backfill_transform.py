"""
Manually triggers the transform Lambda for every existing raw S3 file.
Run once after Phase 4 deploy to process emails already in S3.

Usage:
    uv run scripts/backfill_transform.py
"""
import json
import os
import boto3
from dotenv import load_dotenv

load_dotenv()

AWS_REGION  = os.getenv("AWS_REGION", "us-west-2")
BUCKET_NAME = os.environ["S3_BUCKET_NAME"]       # add this to your .env
LAMBDA_NAME = "gmail-txn-prod-transform"


def build_s3_event(bucket: str, key: str) -> dict:
    """Mimic the S3 event structure Lambda receives."""
    return {
        "Records": [{
            "s3": {
                "bucket": {"name": bucket},
                "object": {"key": key},
            }
        }]
    }


def main():
    s3  = boto3.client("s3", region_name=AWS_REGION)
    lam = boto3.client("lambda", region_name=AWS_REGION)

    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=BUCKET_NAME, Prefix="raw/")

    keys = [
        obj["Key"]
        for page in pages
        for obj in page.get("Contents", [])
        if obj["Key"].endswith(".json")
    ]

    print(f"Found {len(keys)} files to backfill\n")

    ok = fail = 0
    for key in keys:
        event = build_s3_event(BUCKET_NAME, key)
        resp  = lam.invoke(
            FunctionName=LAMBDA_NAME,
            InvocationType="RequestResponse",
            Payload=json.dumps(event).encode(),
        )
        result = json.loads(resp["Payload"].read())
        if resp.get("FunctionError"):
            print(f"  ✗ {key} — {result.get('errorMessage')}")
            fail += 1
        else:
            print(f"  ✓ {key}")
            ok += 1

    print(f"\nDone — {ok} succeeded, {fail} failed")


if __name__ == "__main__":
    main() 