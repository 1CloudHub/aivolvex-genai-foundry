import os
import json
import boto3
import requests
from typing import List, Dict, Any
from botocore.exceptions import ClientError

# ── Media streaming config ──────────────────────────────────────────────────
MEDIA_STREAMING_BUCKET = os.environ.get("MEDIA_BUCKET_NAME", "public-media-sandbox")
MEDIA_STREAMING_REGION = os.environ.get("AWS_REGION", "us-west-2")
MEDIA_PRESIGN_EXPIRES = int(os.environ.get("PRESIGNED_URL_EXPIRY", 3600))

# ── Customer feedback config ────────────────────────────────────────────────
CUSTOMER_FEEDBACK_APPS_SCRIPT_URL = os.environ.get("CUSTOMER_FEEDBACK_APPS_SCRIPT_URL", "https://script.google.com/macros/s/AKfycbyJDaaaX6t_sxTsMIfcNThfyU3n-2EWJ3hESQ1uOXF7clyJeWjpW0zDwIjKuub8lvcn/exec")

# ── Media map (media_id → S3 key) ───────────────────────────────────────────
MEDIA_STREAMING_OBJECTS = {
    1: "climatechangemodified.mp4",
    2: "YTDown.com_YouTube_Musical-resistance-in-Argentina-Children_Media_9Lc2X10tjPw_002_720p.mp4",
    3: "Blacksummer.mp4",
    4: "Manila lockdown.mp4",
}

def handle_media_streaming_event(event_dict):
    """Build a fresh presigned URL for the object mapped by media_id."""
    raw = event_dict.get("media_id")
    if raw is None:
        return {"ok": False, "error": "media_id is required", "event_type": "media_streaming"}
    try:
        media_id = int(raw)
    except (TypeError, ValueError):
        return {"ok": False, "error": "media_id must be an integer", "event_type": "media_streaming"}

    key = MEDIA_STREAMING_OBJECTS.get(media_id)
    if not key:
        return {
            "ok": False,
            "error": f"Unknown media_id {media_id}",
            "valid_media_ids": list(MEDIA_STREAMING_OBJECTS.keys()),
            "event_type": "media_streaming",
        }

    try:
        s3 = boto3.client("s3", region_name=MEDIA_STREAMING_REGION)
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": MEDIA_STREAMING_BUCKET, "Key": key},
            ExpiresIn=MEDIA_PRESIGN_EXPIRES,
        )
    except ClientError as e:
        return {
            "ok": False,
            "error": "Failed to generate presigned URL",
            "detail": str(e),
            "event_type": "media_streaming",
        }

    return {
        "ok": True,
        "event_type": "media_streaming",
        "media_id": media_id,
        "presigned_url": url,
        "bucket": MEDIA_STREAMING_BUCKET,
        "key": key,
        "expires_in": str(MEDIA_PRESIGN_EXPIRES),
    }


def _build_customer_feedback_payload(row: Dict[str, Any]) -> Dict[str, str]:
    """Build form field dict for one feedback row. sub_section/answer are optional (empty allowed)."""
    qn = row.get("question_no")
    if qn is None or (isinstance(qn, str) and not str(qn).strip()):
        question_no_str = ""
    else:
        question_no_str = str(qn).strip()
    return {
        "full_name": str(row.get("full_name", "")).strip(),
        "email": str(row.get("email", "")).strip(),
        "company_name": str(row.get("company_name", "")).strip(),
        "phone_number": str(row.get("phone_number", "")).strip(),
        "industry": str(row.get("industry", "")).strip(),
        "section": str(row.get("section", "")).strip(),
        "sub_section": str(row.get("sub_section", "")).strip(),
        "question_no": question_no_str,
        "answer": str(row.get("answer", "")).strip(),
    }


def submit_customer_feedback(event_dict):
    """Send customer feedback to Google Apps Script in one bulk request."""
    apps_script_url = str(
        event_dict.get("apps_script_url")
        or CUSTOMER_FEEDBACK_APPS_SCRIPT_URL
    ).strip()
    if not apps_script_url:
        return {
            "statusCode": 400,
            "event_type": "customer_feedback",
            "message": "Apps Script URL is missing. Set 'apps_script_url' or CUSTOMER_FEEDBACK_APPS_SCRIPT_URL.",
        }

    raw_batch = event_dict.get("customer_feedback_batch")
    if raw_batch is None:
        raw_batch = event_dict.get("items")
    if isinstance(raw_batch, list):
        rows = raw_batch
    else:
        rows = [event_dict]

    if not rows:
        return {
            "statusCode": 400,
            "event_type": "customer_feedback",
            "message": "No feedback rows to submit",
        }

    payloads: List[Dict[str, str]] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        payload = _build_customer_feedback_payload(row)
        payloads.append(payload)

    if not payloads:
        return {
            "statusCode": 400,
            "event_type": "customer_feedback",
            "message": "No valid feedback rows to submit",
        }

    try:
        apps_script_payload = {
            "event_type": "customer_feedback",
            "items": payloads,
        }
        response = requests.post(apps_script_url, json=apps_script_payload, timeout=30)
        ok = 200 <= response.status_code < 300
        response_text = (response.text or "")[:4000]
    except Exception as e:
        return {
            "statusCode": 500,
            "event_type": "customer_feedback",
            "message": f"Failed to send customer feedback: {str(e)}",
        }
    return {
        "statusCode": 200 if ok else 502,
        "event_type": "customer_feedback",
        "message": "Bulk payload sent to Apps Script" if ok else "Apps Script bulk request failed",
        "total": len(payloads),
        "apps_script_status_code": response.status_code,
        "apps_script_response": response_text,
    }


def lambda_handler(event, context):
    event_type = (event or {}).get("event_type")

    if event_type == "media_streaming":
        try:
            _ms = handle_media_streaming_event(event)
            _code = 200 if _ms.get("ok") else 400
        except Exception as _e:
            print(f"media_streaming error: {_e}")
            _ms = {"ok": False, "error": str(_e), "event_type": "media_streaming"}
            _code = 500
        return {
            "statusCode": _code,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(_ms),
        }

    if event_type == "customer_feedback":
        try:
            _cf = submit_customer_feedback(event)
            _code = _cf.get("statusCode", 200)
        except Exception as _e:
            print(f"customer_feedback error: {_e}")
            _cf = {"ok": False, "error": str(_e), "event_type": "customer_feedback"}
            _code = 500
        return {
            "statusCode": _code,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(_cf),
        }

    return {
        "statusCode": 400,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "ok": False,
            "error": "Unsupported or missing event_type",
            "event_type": event_type,
            "supported_event_types": ["media_streaming", "customer_feedback"],
        }),
    }

