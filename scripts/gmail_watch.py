"""
One-time local setup script.

Runs the Gmail OAuth flow, registers a push watch on your inbox,
and stores the refresh token in AWS Secrets Manager so Lambda can
use it without ever doing an interactive login.

Usage:
    uv run scripts/gmail_watch.py
"""

import json
import os
import sys

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
CREDENTIALS_FILE = "SECRET/credentials.json"
SSM_HISTORY_PARAM = "/gmail-txn/prod/history-id"

PROJECT_ID = os.environ["GOOGLE_CLOUD_PROJECT_ID"]
TOPIC_NAME = os.getenv("PUBSUB_TOPIC_NAME")
PUBSUB_TOPIC = f"projects/{PROJECT_ID}/topics/{TOPIC_NAME}"


AWS_REGION = os.getenv("AWS_REGION")
SECRET_NAME = "gmail-txn/oauth-credentials"


# ── Steps ─────────────────────────────────────────────────────────────────────


def run_oauth_flow():
    """Open browser for OAuth consent and return credentials."""
    if not os.path.exists(CREDENTIALS_FILE):
        sys.exit(f"ERROR: {CREDENTIALS_FILE} not found. Download it from Google Console first.")

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=8080, prompt="consent")
    print("✓ OAuth flow complete")
    return creds


def register_gmail_watch(creds):
    """Tell Gmail to push new-mail events to the Pub/Sub topic."""
    service = build("gmail", "v1", credentials=creds)
    response = service.users().watch(
        userId="me",
        body={
            "topicName": PUBSUB_TOPIC,
            "labelIds": ["INBOX"],
            "labelFilterBehavior": "INCLUDE",
        },
    ).execute()

    expiry_ms = int(response["expiration"])
    from datetime import datetime, timezone
    expiry = datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc)

    print(f"✓ Gmail watch registered")
    print(f"  Topic    : {PUBSUB_TOPIC}")
    print(f"  HistoryId: {response['historyId']}")
    print(f"  Expires  : {expiry.strftime('%Y-%m-%d %H:%M UTC')}  (renews every 7 days via Lambda)")
    return response


def store_credentials_in_ssm(creds, watch_result):
    ssm = boto3.client("ssm", region_name=AWS_REGION)

    # Store OAuth credentials as a single SecureString parameter
    ssm.put_parameter(
        Name="/gmail-txn/prod/oauth-credentials",
        Value=json.dumps({
            "client_id":     creds.client_id,
            "client_secret": creds.client_secret,
            "refresh_token": creds.refresh_token,
            "token_uri":     "https://oauth2.googleapis.com/token",
        }),
        Type="SecureString",
        Overwrite=True,
    )
    print("✓ OAuth credentials stored in SSM")

    ssm.put_parameter(
        Name="/gmail-txn/prod/history-id",
        Value=str(watch_result["historyId"]),
        Type="String",
        Overwrite=True,
    )
    print(f"✓ historyId stored in SSM: {watch_result['historyId']}")


def store_history_id_in_ssm(watch_result):
    """Write the initial historyId to SSM Parameter Store."""
    ssm = boto3.client("ssm", region_name=AWS_REGION)
    ssm.put_parameter(
        Name=SSM_HISTORY_PARAM,
        Value=str(watch_result["historyId"]),
        Type="String",
        Overwrite=True,
    )
    print(f"✓ historyId stored in SSM: {watch_result['historyId']}")


if __name__ == "__main__":
    print("\n── Gmail Transaction Pipeline — One-time Setup ──\n")
    creds = run_oauth_flow()
    watch = register_gmail_watch(creds)
    store_credentials_in_ssm(creds, watch)
    store_history_id_in_ssm(watch)
    print("\n✓ All done.\n")