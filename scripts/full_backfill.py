"""
Full Gmail history backfill.

Searches your entire mailbox for emails from BANK_EMAIL_SENDER,
classifies each one, and handles it accordingly:

  transaction        → write to S3  (transform Lambda fires automatically)
  login_notification → trash
  marketing          → skip

Safe to re-run — S3 writes are idempotent (same key = same content).

Usage:
    uv run scripts/full_backfill.py
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import boto3
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
AWS_REGION        = os.getenv("AWS_REGION", "us-west-2")
SECRET_NAME       = "gmail-txn/oauth-credentials"
BUCKET_NAME       = os.environ["S3_BUCKET_NAME"]
BANK_EMAIL_SENDER = os.environ["BANK_EMAIL_SENDER"]

_raw_kw       = os.getenv("GMAIL_LOGIN_KEYWORDS", "Login Notification")
LOGIN_KEYWORDS = [kw.strip().lower() for kw in _raw_kw.split(",") if kw.strip()]

TRANSACTION = "transaction"
LOGIN       = "login_notification"
MARKETING   = "marketing"

# Pause between Gmail API calls to stay well within rate limits (10 QPS)
API_DELAY_SECONDS = 0.15


# ── Auth ──────────────────────────────────────────────────────────────────────

def load_credentials() -> Credentials:
    sm     = boto3.client("secretsmanager", region_name=AWS_REGION)
    secret = json.loads(sm.get_secret_value(SecretId=SECRET_NAME)["SecretString"])
    creds  = Credentials(
        token=None,
        refresh_token=secret["refresh_token"],
        token_uri=secret["token_uri"],
        client_id=secret["client_id"],
        client_secret=secret["client_secret"],
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
    )
    creds.refresh(Request())
    return creds


# ── Gmail helpers ─────────────────────────────────────────────────────────────

def list_all_message_ids(service, sender: str) -> list[str]:
    """
    Page through Gmail search results and return every message ID
    from the given sender. May take a moment for large inboxes.
    """
    ids, page_token = [], None
    query = f"from:{sender}"

    logger.info(f"Searching Gmail: {query}")
    while True:
        kwargs = {"userId": "me", "q": query, "maxResults": 500}
        if page_token:
            kwargs["pageToken"] = page_token

        result     = service.users().messages().list(**kwargs).execute()
        batch      = result.get("messages", [])
        ids.extend(m["id"] for m in batch)

        page_token = result.get("nextPageToken")
        if not page_token:
            break
        time.sleep(API_DELAY_SECONDS)

    logger.info(f"Found {len(ids)} emails from {sender}")
    return ids


def fetch_full_email(service, message_id: str) -> dict:
    return (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )


def get_header(message: dict, name: str) -> str:
    for h in message.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def classify(message: dict) -> str:
    subject = get_header(message, "subject")

    if re.search(r"\[(credit|debit):\s*(?:[A-Z]{3}\s*)?[\d]", subject, re.IGNORECASE):
        return TRANSACTION

    if any(kw in subject.lower() for kw in LOGIN_KEYWORDS):
        return LOGIN

    return MARKETING


# ── Actions ───────────────────────────────────────────────────────────────────

def write_to_s3(s3_client, message: dict, message_id: str) -> str:
    internal_ms = int(message.get("internalDate", 0))
    dt = (
        datetime.fromtimestamp(internal_ms / 1000, tz=timezone.utc)
        if internal_ms
        else datetime.now(tz=timezone.utc)
    )
    key = f"raw/{dt.year}/{dt.month:02d}/{dt.day:02d}/{message_id}.json"

    s3_client.put_object(
        Bucket=BUCKET_NAME,
        Key=key,
        Body=json.dumps(message, ensure_ascii=False),
        ContentType="application/json",
    )
    return key


def mark_as_read(service, message_id: str) -> None:
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()


def trash_message(service, message_id: str) -> None:
    service.users().messages().trash(userId="me", id=message_id).execute()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    logger.info("── Vale Full Mailbox Backfill ──\n")

    creds   = load_credentials()
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    s3      = boto3.client("s3", region_name=AWS_REGION)

    message_ids = list_all_message_ids(service, BANK_EMAIL_SENDER)
    total       = len(message_ids)
    

    counts = {TRANSACTION: 0, LOGIN: 0, MARKETING: 0, "errors": 0}

    for idx, msg_id in enumerate(message_ids, 1):
        try:
            message    = fetch_full_email(service, msg_id)
            subject    = get_header(message, "subject")
            email_type = classify(message)

            prefix = f"[{idx}/{total}]"

            if email_type == TRANSACTION:
                key = write_to_s3(s3, message, msg_id)
                mark_as_read(service, msg_id)
                logger.info(f"{prefix} ✓ TRANSACTION  {subject[:60]}")
                logger.info(f"           → {key}")
                counts[TRANSACTION] += 1

            elif email_type == LOGIN:
                trash_message(service, msg_id)
                logger.info(f"{prefix} 🗑  LOGIN        {subject[:60]}")
                counts[LOGIN] += 1

            else:
                logger.info(f"{prefix} –  MARKETING    {subject[:60]}")
                counts[MARKETING] += 1

            time.sleep(API_DELAY_SECONDS)

        except HttpError as exc:
            logger.error(f"[{idx}/{total}] Gmail API error [{msg_id}]: {exc}")
            counts["errors"] += 1
        except Exception as exc:
            logger.error(f"[{idx}/{total}] Failed [{msg_id}]: {exc}", exc_info=True)
            counts["errors"] += 1

    print("\n── Backfill Complete ──")
    print(f"  Transactions written to S3 : {counts[TRANSACTION]}")
    print(f"  Login notifications trashed: {counts[LOGIN]}")
    print(f"  Marketing emails skipped   : {counts[MARKETING]}")
    print(f"  Errors                     : {counts['errors']}")
    print(f"\n  Transform Lambda will now process the {counts[TRANSACTION]} S3 files automatically.")


if __name__ == "__main__":
    main()