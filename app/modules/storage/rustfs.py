"""RustFS（S3 兼容）存储 — 对应 src/storage/rustfs.service.ts。

仅用 PutObject / HeadBucket / CreateBucket；boto3 同步客户端经 asyncio.to_thread 调用。
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_SANITIZE_RE = re.compile(r"[^\w一-鿿.-]")
_SAFE_NAME_RE = re.compile(r"[\w一-鿿.-]")

_client = None
_bucket_ready = False


def is_enabled() -> bool:
    return get_settings().rustfs_enabled


def _get_client():
    global _client
    if _client is None:
        cfg = get_settings()
        _client = boto3.client(
            "s3",
            endpoint_url=cfg.rustfs_endpoint,
            aws_access_key_id=cfg.rustfs_access_key,
            aws_secret_access_key=cfg.rustfs_secret_key,
            region_name=cfg.rustfs_region,
            config=BotoConfig(s3={"addressing_style": "path"}, retries={"max_attempts": 2}),
        )
    return _client


async def ensure_bucket() -> None:
    """HeadBucket 失败则 CreateBucket；已存在或失败均降级为日志。"""
    global _bucket_ready
    if _bucket_ready or not is_enabled():
        return
    cfg = get_settings()
    client = _get_client()

    def _work() -> None:
        try:
            client.head_bucket(Bucket=cfg.rustfs_bucket)
            _bucket_ready = True
            logger.info("RustFS bucket 已存在: %s", cfg.rustfs_bucket)
        except ClientError:
            try:
                client.create_bucket(Bucket=cfg.rustfs_bucket)
                _bucket_ready = True
                logger.info("RustFS bucket 已创建: %s", cfg.rustfs_bucket)
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                    _bucket_ready = True
                else:
                    logger.warning("RustFS bucket 初始化失败: %s", e)

    try:
        await asyncio.to_thread(_work)
    except Exception as e:  # noqa: BLE001
        logger.warning("RustFS bucket 初始化异常: %s", e)


def _sanitize_base_name(name: str) -> str:
    base = _SANITIZE_RE.sub("_", name)
    matched = _SAFE_NAME_RE.findall(base)
    return ("".join(matched) or "file")[:64]


def _guess_ext(content_type: str | None, data: bytes) -> str:
    """按 content-type 或魔法字节推断图片扩展名；否则回退按文件名。"""
    ct = (content_type or "").lower()
    if ct in ("image/png",):
        return ".png"
    if ct in ("image/jpeg", "image/jpg"):
        return ".jpg"
    if ct in ("image/webp",):
        return ".webp"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ""


async def upload_bytes(
    data: bytes, *, file_name: str, content_type: str | None, prefix: str | None = None
) -> str:
    """上传并返回公开 URL：{publicBase}/{bucket}/{prefix|documents}/{yyyy/MM/dd}/{name}-{uuid}{ext}"""
    cfg = get_settings()
    client = _get_client()

    from pathlib import PurePosixPath

    ext = PurePosixPath(file_name).suffix
    if file_name and not ext:
        ext = _guess_ext(content_type, data)
    base = _sanitize_base_name(PurePosixPath(file_name).stem or "file")
    date_part = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    key = f"{prefix or 'documents'}/{date_part}/{base}-{uuid.uuid4()}{ext}"

    def _work() -> None:
        client.put_object(
            Bucket=cfg.rustfs_bucket,
            Key=key,
            Body=data,
            ContentType=content_type or "application/octet-stream",
        )

    await asyncio.to_thread(_work)
    return f"{cfg.rustfs_public_url_resolved}/{cfg.rustfs_bucket}/{key}"
