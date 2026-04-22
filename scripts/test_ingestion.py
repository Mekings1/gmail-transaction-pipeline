"""
Manually invoke the ingestion Lambda with a mock Pub/Sub notification.
Uses the current historyId stored in Secrets Manager as the trigger.

Usage:
    uv run scripts/test_ingestion.py
"""

import base64
import json
import os

import boto3
from dotenv import load_dotenv

load_dotenv()

AWS_REGION   = os.getenv("AWS_REGION", "us-west-2")
SECRET_NAME  = "gmail-txn/oauth-credentials"
LAMBDA_NAME  = "gmail-txn-prod-ingestion"
PROJECT_ID   = os.environ["GOOGLE_CLOUD_PROJECT_ID"]
TOPIC_NAME   = os.getenv("PUBSUB_TOPIC_NAME", "gmail-transactions")


def main():
    ssm        = boto3.client("ssm", region_name=AWS_REGION)
    history_id = ssm.get_parameter(Name="/gmail-txn/prod/history-id")["Parameter"]["Value"]

    email_address = input("Your Gmail address: ").strip()
    print(f"\nUsing historyId: {history_id}")
    print("(Gmail will return any messages added since that ID)\n")

    # Build the Pub/Sub notification body the same way Google sends it
    notification = base64.b64encode(
        json.dumps({"emailAddress": email_address, "historyId": history_id}).encode()
    ).decode()

    api_gw_event = {
        "version": "2.0",
        "routeKey": "POST /webhook/gmail",
        "rawPath": "/webhook/gmail",
        "headers": {"content-type": "application/json"},
        "isBase64Encoded": False,
        "body": json.dumps({
            "message": {
                "data": notification,
                "messageId": "test-manual-001",
                "publishTime": "2026-05-15T19:00:00Z",
            },
            "subscription": (
                f"projects/{PROJECT_ID}/subscriptions/{TOPIC_NAME}-push"
            ),
        }),
    }

    # Invoke Lambda synchronously
    lam = boto3.client("lambda", region_name=AWS_REGION)
    response = lam.invoke(
        FunctionName=LAMBDA_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(api_gw_event).encode(),
    )

    payload = json.loads(response["Payload"].read())
    print("Lambda response:")
    print(json.dumps(payload, indent=2))

    if response.get("FunctionError"):
        print(f"\n✗ Function error: {response['FunctionError']}")
    else:
        print("\n✓ Lambda completed — check logs and S3 below")


if __name__ == "__main__":
    main()