import uuid
from typing import Optional

from app.config import settings


def upload_audio(data: bytes, user_id: uuid.UUID, session_id: uuid.UUID) -> Optional[str]:
    """Upload raw PCM audio to R2. Returns R2 key, or None if credentials not configured."""
    if not all([settings.r2_endpoint_url, settings.r2_access_key, settings.r2_secret_key]):
        return None

    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    key = f"audio/{user_id}/{session_id}.pcm"
    try:
        boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key,
            aws_secret_access_key=settings.r2_secret_key,
        ).put_object(Bucket=settings.r2_bucket, Key=key, Body=data)
        return key
    except (BotoCoreError, ClientError):
        return None
