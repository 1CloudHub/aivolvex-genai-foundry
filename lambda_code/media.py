import os
import json
import boto3
import requests
from botocore.exceptions import ClientError

# ── Media streaming config ──────────────────────────────────────────────────
MEDIA_STREAMING_BUCKET = os.environ.get("MEDIA_BUCKET_NAME", "public-media-sandbox")
MEDIA_STREAMING_REGION = os.environ.get("AWS_REGION", "us-west-2")
MEDIA_PRESIGN_EXPIRES = int(os.environ.get("PRESIGNED_URL_EXPIRY", 3600))

# ── Customer feedback config ────────────────────────────────────────────────
CUSTOMER_FEEDBACK_APPS_SCRIPT_URL = os.environ.get("CUSTOMER_FEEDBACK_APPS_SCRIPT_URL", "")

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


def submit_customer_feedback(event_dict):
    """Send customer feedback fields to Google Apps Script."""
    apps_script_url = str(
        event_dict.get("apps_script_url") or CUSTOMER_FEEDBACK_APPS_SCRIPT_URL
    ).strip()
    if not apps_script_url:
        return {
            "statusCode": 400,
            "event_type": "customer_feedback",
            "message": "Apps Script URL is missing. Set 'apps_script_url' or CUSTOMER_FEEDBACK_APPS_SCRIPT_URL.",
        }

    payload = {
        "full_name": str(event_dict.get("full_name", "")).strip(),
        "phone_number": str(event_dict.get("phone_number", "")).strip(),
        "email": str(event_dict.get("email", "")).strip(),
        "overall_experience": str(event_dict.get("overall_experience", "")).strip(),
        "suggestion": str(event_dict.get("suggestion", "")).strip(),
    }

    missing_fields = [key for key, value in payload.items() if not value]
    if missing_fields:
        return {
            "statusCode": 400,
            "event_type": "customer_feedback",
            "message": "Missing required fields",
            "missing_fields": missing_fields,
        }

    try:
        response = requests.post(apps_script_url, data=payload, timeout=15)
        return {
            "statusCode": response.status_code,
            "event_type": "customer_feedback",
            "message": "Sent to Apps Script",
            "apps_script_response": response.text,
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "event_type": "customer_feedback",
            "message": f"Failed to send customer feedback: {str(e)}",
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

