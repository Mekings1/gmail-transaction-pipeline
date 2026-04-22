"""
Ingestion Lambda

Trigger  : API Gateway POST /webhook/gmail (Pub/Sub push)
           EventBridge scheduled rule (watch renewal every 6 days)

Email classification:
  transaction        → write to S3, mark as read
  login_notification → trash silently, no S3 write
  marketing          → skip entirely, leave untouched
"""

import base64
import json
import logging
import os
import re
from datetime import datetime, timezone

import boto3
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Config ────────────────────────────────────────────────────────────────────
RAW_BUCKET_NAME   = os.environ["RAW_BUCKET_NAME"]
SECRET_NAME       = os.environ["SECRET_NAME"]
BANK_EMAIL_SENDER = os.environ.get("BANK_EMAIL_SENDER", "").lower()
PUBSUB_TOPIC      = os.environ.get("PUBSUB_TOPIC", "")
SSM_HISTORY_PARAM = os.environ.get("HISTORY_ID_PARAM", "/gmail-txn/prod/history-id")


# Comma-separated subject keywords that identify login notification emails
_raw_kw = os.environ.get("GMAIL_LOGIN_KEYWORDS", "Login Notification")
LOGIN_KEYWORDS = [kw.strip().lower() for kw in _raw_kw.split(",") if kw.strip()]

# ── Classification labels ─────────────────────────────────────────────────────
TRANSACTION = "transaction"
LOGIN       = "login_notification"
MARKETING   = "marketing"

# ── AWS clients (module-level — reused across warm invocations) ───────────────
_secrets = boto3.client("secretsmanager")
_s3      = boto3.client("s3")
_ssm     = boto3.client("ssm")

# ── Credentials ───────────────────────────────────────────────────────────────

def load_credentials() -> Credentials:
    """Load OAuth credentials from Secrets Manager. No historyId here."""
    raw    = _secrets.get_secret_value(SecretId=SECRET_NAME)
    secret = json.loads(raw["SecretString"])
    creds  = Credentials(
        token=None,
        refresh_token=secret["refresh_token"],
        token_uri=secret["token_uri"],
        client_id=secret["client_id"],
        client_secret=secret["client_secret"],
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
    )
    creds.refresh(Request())
    logger.info("OAuth credentials refreshed")
    return creds


def get_history_id() -> str:
    """Read the current historyId cursor from SSM Parameter Store."""
    try:
        resp = _ssm.get_parameter(Name=SSM_HISTORY_PARAM)
        return resp["Parameter"]["Value"]
    except _ssm.exceptions.ParameterNotFound:
        logger.warning("No historyId in SSM — starting fresh")
        return "0"


def save_history_id(history_id: str) -> None:
    """Overwrite the historyId cursor in SSM. No versioning, no limits."""
    _ssm.put_parameter(
        Name=SSM_HISTORY_PARAM,
        Value=history_id,
        Type="String",
        Overwrite=True,
    )
    logger.info(f"historyId → {history_id}")


# ── Gmail API helpers ─────────────────────────────────────────────────────────

def get_new_message_ids(service, from_history_id: str) -> list[str]:
    """Return deduplicated message IDs added to INBOX since from_history_id."""
    try:
        resp = (
            service.users()
            .history()
            .list(
                userId="me",
                startHistoryId=from_history_id,
                historyTypes=["messageAdded"],
                labelId="INBOX",
            )
            .execute()
        )
    except HttpError as exc:
        if exc.resp.status == 404:
            logger.warning(f"historyId {from_history_id} expired — skipping")
            return []
        raise

    ids = [
        added["message"]["id"]
        for record in resp.get("history", [])
        for added in record.get("messagesAdded", [])
    ]
    return list(dict.fromkeys(ids))  # deduplicate, preserve order


def fetch_full_email(service, message_id: str) -> dict:
    return (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )


def mark_as_read(service, message_id: str) -> None:
    """Remove UNREAD label — clears the unread badge in Gmail."""
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()


def trash_message(service, message_id: str) -> None:
    """Move to trash — recoverable for 30 days."""
    service.users().messages().trash(userId="me", id=message_id).execute()


# ── Email classification ──────────────────────────────────────────────────────

def get_header_value(message: dict, name: str) -> str:
    for h in message.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def is_from_bank(message: dict) -> bool:
    if not BANK_EMAIL_SENDER:
        return True
    return BANK_EMAIL_SENDER in get_header_value(message, "from").lower()


def classify(message: dict) -> str:
    """
    Classify a bank email by subject.

    transaction        Subject contains [Credit: NGN X] or [Debit: NGN X]
    login_notification Subject contains any keyword in GMAIL_LOGIN_KEYWORDS
    marketing          Everything else
    """
    subject = get_header_value(message, "subject")

    if re.search(r"\[(credit|debit):\s*(?:[A-Z]{3}\s*)?[\d]", subject, re.IGNORECASE):
        return TRANSACTION

    subject_lower = subject.lower()
    if any(kw in subject_lower for kw in LOGIN_KEYWORDS):
        return LOGIN

    return MARKETING


# ── S3 storage ────────────────────────────────────────────────────────────────

def write_to_s3(message: dict, message_id: str) -> str:
    """
    Write raw email JSON to S3.
    Uses internalDate (not today) so historical emails land in the right folder.
    Path: raw/YYYY/MM/DD/{messageId}.json
    """
    internal_ms = int(message.get("internalDate", 0))
    dt = (
        datetime.fromtimestamp(internal_ms / 1000, tz=timezone.utc)
        if internal_ms
        else datetime.now(tz=timezone.utc)
    )
    key = f"raw/{dt.year}/{dt.month:02d}/{dt.day:02d}/{message_id}.json"

    _s3.put_object(
        Bucket=RAW_BUCKET_NAME,
        Key=key,
        Body=json.dumps(message, ensure_ascii=False),
        ContentType="application/json",
    )
    logger.info(f"S3 ← s3://{RAW_BUCKET_NAME}/{key}")
    return key


# ── Handler ───────────────────────────────────────────────────────────────────

def lambda_handler(event, context):

    # ── Scheduled watch renewal ───────────────────────────────────────────────
    if event.get("source") == "scheduled-renewal":
        creds = load_credentials()
        svc   = build("gmail", "v1", credentials=creds, cache_discovery=False)
        result = svc.users().watch(
            userId="me",
            body={"topicName": PUBSUB_TOPIC, "labelIds": ["INBOX"]},
        ).execute()
        logger.info(f"Watch renewed — expires {result.get('expiration')}")
        return {"statusCode": 200, "body": "watch renewed"}

    # ── Parse Pub/Sub push ────────────────────────────────────────────────────
    try:
        body                = json.loads(event.get("body") or "{}")
        data_b64            = body["message"]["data"]
        notification        = json.loads(base64.b64decode(data_b64).decode("utf-8"))
        incoming_history_id = str(notification["historyId"])
        logger.info(f"Pub/Sub historyId: {incoming_history_id}")
    except (KeyError, ValueError) as exc:
        logger.error(f"Bad Pub/Sub payload: {exc}")
        return {"statusCode": 200, "body": "bad payload — skipped"}

    # ── Auth + fetch ──────────────────────────────────────────────────────────
    creds           = load_credentials()
    last_history_id = get_history_id()
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    message_ids = get_new_message_ids(service, last_history_id)

    if not message_ids:
        save_history_id(incoming_history_id)
        return {"statusCode": 200, "body": "no new messages"}

    # ── Classify and act ──────────────────────────────────────────────────────
    counts = {TRANSACTION: 0, LOGIN: 0, MARKETING: 0, "errors": 0}

    for msg_id in message_ids:
        try:
            message    = fetch_full_email(service, msg_id)
            subject    = get_header_value(message, "subject")

            if not is_from_bank(message):
                logger.info(f"Not from bank — skip {msg_id}")
                continue

            email_type = classify(message)
            logger.info(f"[{email_type.upper()}] {subject}")

            if email_type == TRANSACTION:
                write_to_s3(message, msg_id)
                mark_as_read(service, msg_id)
                counts[TRANSACTION] += 1

            elif email_type == LOGIN:
                trash_message(service, msg_id)
                counts[LOGIN] += 1

            else:
                counts[MARKETING] += 1

        except HttpError as exc:
            logger.error(f"Gmail API error [{msg_id}]: {exc}")
            counts["errors"] += 1
        except Exception as exc:
            logger.error(f"Unexpected error [{msg_id}]: {exc}", exc_info=True)
            counts["errors"] += 1

    save_history_id(incoming_history_id)
    logger.info(f"Done — {counts}")
    return {"statusCode": 200, "body": json.dumps(counts)}