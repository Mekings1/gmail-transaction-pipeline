"""
Transform Lambda

Trigger  : S3 PUT event on raw/{year}/{month}/{day}/{messageId}.json
What it does:
  1. Reads raw Gmail JSON from S3
  2. Parses Vale transaction email — subject + plain text body
  3. Writes a clean structured record to DynamoDB
"""

import base64
import json
import logging
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Config ────────────────────────────────────────────────────────────────────
DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]

# ── AWS clients ───────────────────────────────────────────────────────────────
_s3 = boto3.client("s3")
_table = boto3.resource("dynamodb").Table(DYNAMODB_TABLE_NAME)


# ── Email parsing helpers ─────────────────────────────────────────────────────

def get_header(headers: list[dict], name: str) -> str:
    """Return the value of the first header matching name (case-insensitive)."""
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def decode_body(data: str) -> str:
    """Decode Gmail's URL-safe base64 body data to a plain string."""
    # Gmail omits padding — add it back before decoding
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="ignore")


def get_plain_text(payload: dict) -> str:
    """
    Walk the message payload to find the text/plain part.
    Handles both multipart and single-part messages.
    """
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain":
            return decode_body(part["body"].get("data", ""))

    # Single-part message — body is directly on the payload
    return decode_body(payload.get("body", {}).get("data", ""))


# ── Field extractors ──────────────────────────────────────────────────────────

def parse_subject(subject: str) -> tuple[str, Decimal, str]:
    """
    Extract transaction type, amount, and currency from Vale subject line.

    Examples:
      "Vale - [Credit: NGN 5.88] Transaction Alert"
      "Vale - [Debit: NGN 2,500.00] Transaction Alert"

    Returns: (transaction_type, amount, currency)
    Raises: ValueError if the subject doesn't match the expected pattern.
    """
    match = re.search(
    r"\[(Credit|Debit):\s*(?:([A-Z]{3})\s*)?([\d,]+\.?\d*)\]",
    subject,
    re.IGNORECASE,
    )
    if not re.match:
        raise ValueError(f"Subject does not look like a Vale transaction: {subject!r}")

    transaction_type = match.group(1).lower()                                 # "credit" | "debit"
    currency         = match.group(2).upper() if match.group(2) else "NGN"    # "NGN"
    amount            = Decimal(match.group(3).replace(",", ""))              # "2,500.00" → Decimal("2500.00")

    return transaction_type, amount, currency


def parse_text_body(text: str) -> dict:
    """
    Parse Vale's plain-text email body.

    Vale formats transaction details as a label on one line
    followed by the value on the very next non-empty line:

        Account Name
        Interest Pool

        Transaction Description
        Wallet Account Interest for 2026-05-15

        Reference Number
        VFI260516014239383684

        Transaction Date
        2026-05-16 01:42:40

        Available Balance
        NGN 1,581.73

    Returns a dict of extracted fields.
    """
    LABEL_MAP = {
        "account name":           "account_name",
        "transaction description":"description",
        "reference number":       "reference_number",
        "transaction date":       "transaction_date",
        "available balance":      "available_balance_raw",
    }

    # Strip whitespace and drop blank lines
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    fields: dict = {}
    for idx, line in enumerate(lines):
        label = line.lower()
        if label in LABEL_MAP and idx + 1 < len(lines):
            fields[LABEL_MAP[label]] = lines[idx + 1]

    # Convert available balance to Decimal — strip currency prefix & commas
    raw = fields.pop("available_balance_raw", "")
    balance_match = re.search(r"([\d,]+\.?\d*)", raw)
    if balance_match:
        try:
            fields["available_balance"] = Decimal(
                balance_match.group(1).replace(",", "")
            )
        except InvalidOperation:
            fields["available_balance"] = Decimal("0")

    return fields


# ── Record builder ────────────────────────────────────────────────────────────

def build_dynamo_record(message: dict, s3_key: str) -> dict:
    """
    Combine parsed fields into a DynamoDB item.

    Partition key : date  (YYYY-MM-DD)  — lets us query all transactions per day
    Sort key      : sk    (ISO datetime + # + email ID)  — unique within a day
    """
    headers = message.get("payload", {}).get("headers", [])
    subject = get_header(headers, "subject")

    # Parse subject — raises ValueError for non-transaction emails
    transaction_type, amount, currency = parse_subject(subject)

    # Parse body
    body_text = get_plain_text(message.get("payload", {}))
    fields = parse_text_body(body_text)

    # Build sort key from transaction date
    transaction_date_str = fields.get("transaction_date", "")
    try:
        txn_dt = datetime.strptime(transaction_date_str, "%Y-%m-%d %H:%M:%S")
        date_str = txn_dt.strftime("%Y-%m-%d")
        sk = f"{txn_dt.isoformat()}#{message['id']}"
    except ValueError:
        now = datetime.now(tz=timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        sk = f"{now.isoformat()}#{message['id']}"
        logger.warning(f"Could not parse transaction date {transaction_date_str!r}, using now")

    return {
        # Keys
        "date":               date_str,
        "sk":                 sk,
        # Transaction core
        "transaction_type":   transaction_type,
        "amount":             amount,
        "currency":           currency,
        # Transaction detail
        "account_name":       fields.get("account_name", ""),
        "description":        fields.get("description", ""),
        "reference_number":   fields.get("reference_number", ""),
        "transaction_date":   transaction_date_str,
        "available_balance":  fields.get("available_balance", Decimal("0")),
        # Pipeline metadata
        "email_id":           message["id"],
        "raw_s3_key":         s3_key,
        "ingested_at":        datetime.now(tz=timezone.utc).isoformat(),
    }


# ── Handler ───────────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    logger.info("Records to process: %d", len(event.get("Records", [])))

    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key    = record["s3"]["object"]["key"]
        logger.info(f"Reading s3://{bucket}/{key}")

        try:
            obj = _s3.get_object(Bucket=bucket, Key=key)
            message = json.loads(obj["Body"].read())

            item = build_dynamo_record(message, key)

            _table.put_item(Item=item)

            logger.info(
                "✓ Saved | %s | %s | %s %s | %s",
                item["date"],
                item["transaction_type"].upper(),
                item["currency"],
                item["amount"],
                item["description"],
            )

        except ValueError as exc:
            # Subject didn't match — not a transaction email, safe to skip
            logger.warning(f"Skipping {key} — {exc}")

        except Exception as exc:
            logger.error(f"Failed on {key}: {exc}", exc_info=True)
            raise  # Let Lambda retry this record

    return {"statusCode": 200, "body": "done"}