import os
import logging
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger("object_storage")

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "cbc-research-files")
MINIO_SECURE = os.getenv("MINIO_USE_SSL", "false").lower() == "true"

def get_s3_client():
    try:
        import boto3
        from botocore.client import Config

        endpoint_url = f"http{'s' if MINIO_SECURE else ''}://{MINIO_ENDPOINT}"
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=MINIO_ACCESS_KEY,
            aws_secret_access_key=MINIO_SECRET_KEY,
            config=Config(signature_version="s3v4"),
            region_name="us-east-1"
        )
        return s3
    except Exception as e:
        logger.warning(f"S3/MinIO client connection failed: {e}")
        return None

def init_minio_bucket():
    client = get_s3_client()
    if not client:
        return False
    try:
        buckets = client.list_buckets()
        bucket_names = [b["Name"] for b in buckets.get("Buckets", [])]
        if MINIO_BUCKET not in bucket_names:
            client.create_bucket(Bucket=MINIO_BUCKET)
            logger.info(f"Created MinIO bucket '{MINIO_BUCKET}' successfully.")
        return True
    except Exception as e:
        logger.warning(f"Error initializing MinIO bucket '{MINIO_BUCKET}': {e}")
        return False

def upload_file(local_path: Union[str, Path], object_name: str) -> Optional[str]:
    client = get_s3_client()
    if not client:
        return None
    try:
        p = Path(local_path)
        if not p.exists():
            return None
        init_minio_bucket()
        client.upload_file(str(p), MINIO_BUCKET, object_name)
        logger.info(f"Uploaded '{local_path}' to MinIO bucket '{MINIO_BUCKET}' as '{object_name}'")
        return object_name
    except Exception as e:
        logger.error(f"Failed uploading file to MinIO: {e}")
        return None

def upload_bytes(data: bytes, object_name: str, content_type: str = "application/octet-stream") -> Optional[str]:
    client = get_s3_client()
    if not client:
        return None
    try:
        init_minio_bucket()
        client.put_object(
            Bucket=MINIO_BUCKET,
            Key=object_name,
            Body=data,
            ContentType=content_type
        )
        logger.info(f"Uploaded {len(data)} bytes to MinIO as '{object_name}'")
        return object_name
    except Exception as e:
        logger.error(f"Failed uploading bytes to MinIO: {e}")
        return None

def download_file(object_name: str, dest_path: Union[str, Path]) -> bool:
    client = get_s3_client()
    if not client:
        return False
    try:
        p = Path(dest_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        client.download_file(MINIO_BUCKET, object_name, str(p))
        return True
    except Exception as e:
        logger.error(f"Failed downloading '{object_name}' from MinIO: {e}")
        return False

def get_file_bytes(object_name: str) -> Optional[bytes]:
    client = get_s3_client()
    if not client:
        return None
    try:
        resp = client.get_object(Bucket=MINIO_BUCKET, Key=object_name)
        return resp["Body"].read()
    except Exception as e:
        logger.error(f"Failed getting object bytes for '{object_name}': {e}")
        return None
