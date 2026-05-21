"""
scripts/renew_watch.py

Manually renews the Gmail push watch outside of the scheduled Lambda.
Use this if you suspect the watch has expired or after changing scopes.

Usage:
    uv run scripts/renew_watch.py
"""

import json
import os

import boto3
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

load_dotenv()

AWS_REGION       = os.getenv("AWS_REGION", "us-west-2")
PROJECT_ID       = os.environ["GOOGLE_CLOUD_PROJECT_ID"]
TOPIC_NAME       = os.getenv("PUBSUB_TOPIC_NAME", "gmail-transactions")
PUBSUB_TOPIC     = f"projects/{PROJECT_ID}/topics/{TOPIC_NAME}"
OAUTH_PARAM      = "/gmail-txn/prod/oauth-credentials"


def load_credentials() -> Credentials:
    ssm    = boto3.client("ssm", region_name=AWS_REGION)
    secret = json.loads(
        ssm.get_parameter(Name=OAUTH_PARAM, WithDecryption=True)["Parameter"]["Value"]
    )
    creds = Credentials(
        token=None,
        refresh_token=secret["refresh_token"],
        token_uri=secret["token_uri"],
        client_id=secret["client_id"],
        client_secret=secret["client_secret"],
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
    )
    creds.refresh(Request())
    return creds


def main():
    print("Renewing Gmail watch manually...\n")

    creds   = load_credentials()
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    result = service.users().watch(
        userId="me",
        body={
            "topicName": PUBSUB_TOPIC,
            "labelIds": ["INBOX"],
        },
    ).execute()

    from datetime import datetime, timezone
    expiry_ms = int(result["expiration"])
    expiry    = datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc)

    print(f"✓ Watch renewed successfully")
    print(f"  Topic   : {PUBSUB_TOPIC}")
    print(f"  Expires : {expiry.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  History : {result['historyId']}")


if __name__ == "__main__":
    main()