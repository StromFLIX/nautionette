"""S3-compatible binary storage and instance-wide output policy.

SQLite owns manifests, reservations and a durable object/deletion ledger, never
new file bytes. S3 operations happen outside the SQLite lock. The output lock
serializes publication/cleanup/migration in the supported single backend process.
"""

from __future__ import annotations

import base64
import errno
import hashlib
import io
import shutil
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import unquote, urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException

from .config import settings
from .db import db

MiB = 1024 * 1024
GiB = 1024 * MiB
# Protocol safety ceilings, not the user-configurable quotas.
HARD_FILE_BYTES = GiB
HARD_PUBLICATION_BYTES = 2 * GiB
TRANSFER_SECONDS = 600
lock = threading.RLock()

DEFAULT_POLICY = {
    "global_limit_bytes": 10 * GiB,
    "chat_limit_bytes": 256 * MiB,
    "max_file_bytes": 20 * MiB,
    "max_publication_bytes": 40 * MiB,
    "minimum_free_bytes": 512 * MiB,
    "retention_days": 0,
    "retention_keep_latest": True,
}


def settings_url() -> str:
    try:
        parsed = urlsplit(settings.public_url)
        _ = parsed.port  # Reject malformed/out-of-range ports as well as malformed IPv6.
    except ValueError:
        return "/settings/storage"
    valid = (
        parsed.scheme in {"http", "https"}
        and parsed.netloc
        and not (
            parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
        )
    )
    return (settings.public_url.rstrip("/") if valid else "") + "/settings/storage"


def failure(code: str, message: str, status: int = 413, **details: Any) -> HTTPException:
    return HTTPException(
        status, {"code": code, "message": message, "settings_url": settings_url(), **details}
    )


def policy(conn: Any = None) -> dict[str, Any]:
    if conn is None:
        saved = db.get_setting("output_storage_policy", {})
    else:
        import json

        row = conn.execute("SELECT value FROM settings WHERE key = 'output_storage_policy'").fetchone()
        saved = json.loads(row[0]) if row else {}
    return {**DEFAULT_POLICY, **saved}


def save_policy(changes: dict[str, Any]) -> dict[str, Any]:
    if set(changes) - DEFAULT_POLICY.keys():
        raise HTTPException(422, "Unknown output storage setting")
    with lock:
        merged = {**policy(), **changes}
        for key, value in merged.items():
            if key == "retention_keep_latest":
                if type(value) is not bool:
                    raise HTTPException(422, "retention_keep_latest must be a boolean")
            elif type(value) is not int or value < (
                1 if key.endswith("limit_bytes") or key.startswith("max_") else 0
            ):
                raise HTTPException(
                    422, f"{key} must be a valid nonnegative integer (quotas must be positive)"
                )
        if (
            merged["max_file_bytes"] > HARD_FILE_BYTES
            or merged["max_publication_bytes"] > HARD_PUBLICATION_BYTES
        ):
            raise HTTPException(422, "Protocol ceilings are 1 GiB per file and 2 GiB per publication")
        if merged["max_file_bytes"] > merged["max_publication_bytes"]:
            raise HTTPException(422, "The file limit cannot exceed the publication limit")
        if merged["retention_days"] > 36500 or any(
            merged[key] > 2**53 - 1 for key in merged if key.endswith("bytes")
        ):
            raise HTTPException(422, "Output storage setting is too large")
        db.set_setting("output_storage_policy", merged)
        return merged


def disk(path: str, label: str) -> dict[str, Any]:
    if not path:
        return {"label": label, "available": False, "reason": "Not exposed by the S3 API"}
    try:
        usage = shutil.disk_usage(path)
        return {
            "label": label,
            "available": True,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "available_bytes": usage.free,
        }
    except OSError:
        return {"label": label, "available": False, "reason": "Backing filesystem is not mounted or readable"}


def require_space(requested: int, *, backend: bool = False) -> None:
    path = settings.data_dir if backend else settings.output_storage_disk_path
    capacity = disk(path, "Backend data filesystem" if backend else "MaxIO backing filesystem")
    if capacity["available"] and capacity["available_bytes"] - requested < policy()["minimum_free_bytes"]:
        raise failure(
            "output_disk_space_low",
            "Insufficient filesystem space or minimum-free-space reserve reached. Remove outputs, "
            "increase physical disk capacity, or review the reserve in Settings → Storage.",
            507,
            requested_bytes=requested,
            available_bytes=capacity["available_bytes"],
            reserve_bytes=policy()["minimum_free_bytes"],
            filesystem=capacity["label"],
        )


def usage(conn: Any, chat_id: str | None = None, exclude_upload: str = "") -> dict[str, int]:
    where = " WHERE o.chat_id = ?" if chat_id else ""
    args = (chat_id,) if chat_id else ()
    row = conn.execute(
        "SELECT COALESCE(SUM(f.size),0), "
        "COALESCE(SUM(CASE WHEN f.preview_key IS NOT NULL THEN f.preview_size "
        "ELSE COALESCE(length(f.preview),0) END),0), COUNT(*) "
        "FROM chat_output_files f JOIN chat_outputs o ON o.id = f.output_id" + where,  # noqa: S608 - static predicates
        args,
    ).fetchone()
    reserved = conn.execute(
        "SELECT COALESCE(SUM(reserved_bytes),0) FROM chat_output_uploads WHERE id != ?"  # noqa: S608 - static predicates
        + (" AND chat_id = ?" if chat_id else ""),
        (exclude_upload, *args),
    ).fetchone()[0]
    # Removed durable objects still occupy quota until S3 confirms deletion.
    # Temporary upload/copy bytes are shown separately and protected by the disk
    # reserve. Do not charge duplicate staging bytes as retained chat documents.
    pending = (
        0
        if chat_id
        else conn.execute(
            "SELECT COALESCE(SUM(size),0) FROM output_storage_objects "
            "WHERE state = 'delete' AND kind != 'staging'"
        ).fetchone()[0]
    )
    return {
        "original_bytes": row[0],
        "preview_bytes": row[1],
        "file_count": row[2],
        "used_bytes": row[0] + row[1],
        "reserved_bytes": reserved,
        "pending_deletion_bytes": pending,
        "charged_bytes": row[0] + row[1] + reserved + pending,
    }


def check_quota(conn: Any, chat_id: str, requested: int, exclude_upload: str = "") -> None:
    limits = policy(conn)
    for scope, key, target in (
        ("instance", "global_limit_bytes", None),
        ("chat", "chat_limit_bytes", chat_id),
    ):
        current = usage(conn, target, exclude_upload)["charged_bytes"]
        if current + requested > limits[key]:
            raise failure(
                "output_storage_limit_exceeded",
                f"The {scope} output storage limit would be exceeded. Remove outputs or increase "
                "the limit in Settings → Storage.",
                scope=scope,
                used_bytes=current,
                limit_bytes=limits[key],
                requested_bytes=requested,
            )


def io_failure(exc: OSError) -> HTTPException:
    if exc.errno == errno.ENOSPC:
        return failure("output_disk_full", "Output storage or its temporary filesystem is full.", 507)
    return failure("output_io_error", "An output storage filesystem operation failed.", 503)


def _storage_error(exc: Exception) -> HTTPException:
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return io_failure(exc)
    code = exc.response.get("Error", {}).get("Code", "") if isinstance(exc, ClientError) else ""
    if code in {"InsufficientStorage", "XMinioStorageFull"}:
        return failure("output_disk_full", "The object store has run out of physical space.", 507)
    return failure(
        "output_storage_unavailable",
        "Output object storage is unavailable. Check MaxIO health and the server-side S3 configuration "
        "in Settings → Storage. No storage credentials or private transfer URLs are shown.",
        503,
    )


class S3Store:
    """Small provider-neutral adapter; path-style SigV4 works with MaxIO."""

    def __init__(self) -> None:
        if not settings.output_s3_access_key or not settings.output_s3_secret_key:
            raise failure("output_storage_unconfigured", "Configure the server's output S3 credentials.", 503)
        try:
            endpoint = urlsplit(settings.output_s3_endpoint)
            _ = endpoint.port
        except ValueError:
            raise failure(
                "output_storage_unconfigured", "The output S3 endpoint must be a valid HTTP(S) origin.", 503
            ) from None
        if (
            endpoint.scheme not in {"http", "https"}
            or not endpoint.hostname
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint.query
            or endpoint.fragment
            or endpoint.path not in {"", "/"}
        ):
            raise failure(
                "output_storage_unconfigured", "The output S3 endpoint must be an HTTP(S) origin.", 503
            )
        self.bucket = settings.output_s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.output_s3_endpoint,
            region_name=settings.output_s3_region,
            aws_access_key_id=settings.output_s3_access_key,
            aws_secret_access_key=settings.output_s3_secret_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=5,
                read_timeout=60,
                retries={"max_attempts": 2, "mode": "standard"},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def call(self, operation: str, **kwargs: Any) -> Any:
        try:
            return getattr(self.client, operation)(Bucket=self.bucket, **kwargs)
        except (BotoCoreError, ClientError, OSError) as exc:
            raise _storage_error(exc) from None

    def ready(self) -> None:
        # Compose creates this private bucket. Other S3 deployments must provision
        # it too; never silently create/publicize buckets or change their policy.
        self.call("head_bucket")
        if self.call("get_bucket_versioning").get("Status"):
            raise failure(
                "output_storage_configuration_error",
                "Use a private, unversioned output bucket. Versioned buckets retain old object "
                "bytes after deletion and cannot provide the expected cleanup/quota behavior.",
                503,
            )

    def upload_url(
        self, key: str, size: int, sha256: str, expires_in: int = TRANSFER_SECONDS
    ) -> dict[str, Any]:
        checksum = base64.b64encode(bytes.fromhex(sha256)).decode()
        try:
            url = self.client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": key,
                    "ContentLength": size,
                    "ContentType": "application/octet-stream",
                    "ChecksumSHA256": checksum,
                },
                ExpiresIn=expires_in,
            )
        except (BotoCoreError, ClientError) as exc:
            raise _storage_error(exc) from None
        # MaxIO 0.4.2 does not percent-decode SignedHeaders before splitting
        # its semicolons. Literal semicolons are valid in a query value and have
        # the SAME SigV4 canonical encoding on standard S3 servers. Normalize
        # only this field; never drop the size/checksum-bound signed headers.
        prefix, marker, tail = url.partition("X-Amz-SignedHeaders=")
        signed, separator, rest = tail.partition("&")
        if marker:
            url = prefix + marker + unquote(signed) + separator + rest
        return {
            "url": url,
            "headers": {
                "Content-Length": str(size),
                "Content-Type": "application/octet-stream",
                "x-amz-checksum-sha256": checksum,
            },
        }

    def download_url(self, key: str) -> str:
        try:
            return self.client.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=TRANSFER_SECONDS
            )
        except (BotoCoreError, ClientError) as exc:
            raise _storage_error(exc) from None

    def put(self, key: str, data: bytes | BinaryIO, size: int) -> None:
        self.call(
            "put_object", Key=key, Body=data, ContentLength=size, ContentType="application/octet-stream"
        )

    def copy(self, source: str, target: str) -> None:
        self.call("copy_object", Key=target, CopySource={"Bucket": self.bucket, "Key": source})

    def open(self, key: str) -> tuple[Any, int]:
        value = self.call("get_object", Key=key)
        return value["Body"], value["ContentLength"]

    def download(self, key: str, target: BinaryIO, size: int, sha256: str) -> None:
        body, length = self.open(key)
        try:
            if length != size:
                raise failure(
                    "output_integrity_error", "Uploaded file size does not match its manifest.", 422
                )
            digest = hashlib.sha256()
            received = 0
            while chunk := body.read(1024 * 1024):
                received += len(chunk)
                if received > size:
                    raise failure("output_integrity_error", "Uploaded file exceeds its declared size.", 422)
                digest.update(chunk)
                target.write(chunk)
            if received != size or digest.hexdigest() != sha256:
                raise failure(
                    "output_integrity_error", "Uploaded file checksum does not match its manifest.", 422
                )
            target.seek(0)
        except (BotoCoreError, OSError) as exc:
            raise _storage_error(exc) from None
        finally:
            body.close()

    def bytes(self, key: str, size: int, sha256: str) -> bytes:
        target = io.BytesIO()
        self.download(key, target, size, sha256)
        return target.getvalue()

    def delete(self, key: str) -> None:
        self.call("delete_object", Key=key)


@lru_cache(maxsize=1)
def get_store() -> S3Store:
    return S3Store()


def safe_endpoint() -> str:
    try:
        endpoint = urlsplit(settings.output_s3_endpoint)
        _ = endpoint.port
    except ValueError:
        return "Invalid server-side S3 endpoint (details hidden)"
    if endpoint.username is not None or endpoint.password is not None or endpoint.query or endpoint.fragment:
        return "Invalid server-side S3 endpoint (details hidden)"
    return settings.output_s3_endpoint


def status() -> dict[str, Any]:
    with db.transaction() as conn:
        current = usage(conn)
        counts = {
            "output_count": conn.execute("SELECT COUNT(*) FROM chat_outputs").fetchone()[0],
            "revision_count": conn.execute("SELECT COUNT(*) FROM chat_output_revisions").fetchone()[0],
            "legacy_file_count": conn.execute(
                "SELECT COUNT(*) FROM chat_output_files WHERE object_key IS NULL"
            ).fetchone()[0],
            "temporary_bytes": conn.execute(
                "SELECT COALESCE(SUM(size),0) FROM output_storage_objects "
                "WHERE kind = 'staging' OR state = 'pending'"
            ).fetchone()[0],
            "pending_deletion_objects": conn.execute(
                "SELECT COUNT(*) FROM output_storage_objects WHERE state = 'delete'"
            ).fetchone()[0],
        }
        free_pages = conn.execute("PRAGMA freelist_count").fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    health = {"status": "ok"}
    try:
        get_store().ready()
    except HTTPException as exc:
        health = {"status": "unavailable", "error": exc.detail}
    database_bytes = 0
    for suffix in ("", "-wal"):
        try:
            database_bytes += Path(db.path + suffix).stat().st_size
        except FileNotFoundError:
            pass  # A concurrent checkpoint may have removed the WAL.
    return {
        "provider": "S3-compatible (MaxIO in Compose)",
        "endpoint": safe_endpoint(),
        "bucket": settings.output_s3_bucket,
        "region": settings.output_s3_region,
        "health": health,
        "policy": policy(),
        "usage": {**current, **counts},
        "filesystems": [
            disk(settings.output_storage_disk_path, "MaxIO backing filesystem"),
            disk(settings.data_dir, "Backend data filesystem"),
        ],
        "database": {"bytes": database_bytes, "reclaimable_bytes": free_pages * page_size},
        "maintenance": db.get_setting("output_storage_maintenance", {}),
        "settings_url": settings_url(),
        "maxio_notice": "MaxIO is under active upstream development and is not yet recommended for "
        "production. Back up its volume and the metadata database together.",
    }
