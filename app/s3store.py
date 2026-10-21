"""S3 access for test files. Works with any S3-compatible endpoint."""

import asyncio
import time
from pathlib import PurePosixPath

import boto3
from botocore.config import Config

from . import settings

TYPE_GROUPS = {
    "PDF": {".pdf"},
    "DOCX": {".docx"},
    "DOC": {".doc"},
    "PPTX": {".pptx"},
    "PPT": {".ppt"},
    "XLSX": {".xlsx", ".xls"},
    "EML": {".eml", ".msg"},
    "Images": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"},
    "HTML": {".html", ".htm"},
    "Text": {".txt", ".md", ".csv", ".rtf"},
}

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".eml": "message/rfc822",
    ".msg": "application/vnd.ms-outlook",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".rtf": "application/rtf",
}


def file_type(key: str) -> str:
    ext = PurePosixPath(key).suffix.lower()
    for group, exts in TYPE_GROUPS.items():
        if ext in exts:
            return group
    return "Other"


def content_type(key: str) -> str:
    return CONTENT_TYPES.get(PurePosixPath(key).suffix.lower(), "application/octet-stream")


class S3Error(Exception):
    pass


_client = None
_client_version = -1


def _get_client():
    global _client, _client_version
    if _client is not None and _client_version == settings.version():
        return _client
    cfg = settings.get("s3")
    if not cfg["endpoint"] or not cfg["bucket"]:
        raise S3Error("S3 is not set up yet. Add the endpoint and bucket in Settings, under File storage.")
    verify: bool | str = cfg["verify_tls"]
    if verify:
        verify = settings.ca_bundle_path() or True
    _client = boto3.client(
        "s3",
        endpoint_url=cfg["endpoint"],
        region_name=cfg["region"] or "us-east-1",
        aws_access_key_id=cfg["access_key"] or None,
        aws_secret_access_key=cfg["secret_key"] or None,
        verify=verify,
        config=Config(
            s3={"addressing_style": "path" if cfg["addressing"] == "path" else "virtual"},
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=10,
            read_timeout=120,
            signature_version="s3v4",
        ),
    )
    _client_version = settings.version()
    return _client


def bucket() -> str:
    return settings.get("s3")["bucket"]


def root() -> str:
    r = settings.get("s3")["root"].strip("/")
    return f"{r}/" if r else ""


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except S3Error:
        raise
    except Exception as exc:  # botocore raises many types; show the message to the user
        raise S3Error(f"S3 request failed: {exc}") from exc


def _list_sync(prefix: str) -> dict:
    client = _get_client()
    folders, files = [], []
    token = None
    while True:
        kwargs = {"Bucket": bucket(), "Prefix": prefix, "Delimiter": "/", "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        for cp in resp.get("CommonPrefixes", []):
            folders.append(cp["Prefix"])
        for obj in resp.get("Contents", []):
            if obj["Key"].endswith("/"):
                continue  # folder marker
            files.append({"key": obj["Key"], "size": obj["Size"], "etag": obj["ETag"].strip('"'),
                          "modified": obj["LastModified"].timestamp()})
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return {"folders": folders, "files": files}


def _list_recursive_sync(prefix: str) -> list[dict]:
    client = _get_client()
    files = []
    token = None
    while True:
        kwargs = {"Bucket": bucket(), "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            if obj["Key"].endswith("/"):
                continue
            files.append({"key": obj["Key"], "size": obj["Size"], "etag": obj["ETag"].strip('"'),
                          "modified": obj["LastModified"].timestamp()})
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return files


async def list_folder(prefix: str) -> dict:
    return await asyncio.to_thread(_wrap, _list_sync, prefix)


_recursive_cache: dict[str, tuple[float, list]] = {}


async def list_recursive(prefix: str, max_age: float = 0) -> list[dict]:
    hit = _recursive_cache.get(prefix)
    if hit and max_age and time.time() - hit[0] < max_age:
        return hit[1]
    files = await asyncio.to_thread(_wrap, _list_recursive_sync, prefix)
    _recursive_cache[prefix] = (time.time(), files)
    return files


def invalidate_cache() -> None:
    _recursive_cache.clear()


async def get_bytes(key: str) -> bytes:
    def _get():
        return _get_client().get_object(Bucket=bucket(), Key=key)["Body"].read()
    return await asyncio.to_thread(_wrap, _get)


async def head(key: str) -> dict | None:
    def _head():
        client = _get_client()
        try:
            resp = client.head_object(Bucket=bucket(), Key=key)
        except client.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return {"etag": resp["ETag"].strip('"'), "size": resp["ContentLength"]}
    return await asyncio.to_thread(_wrap, _head)


async def put(key: str, data: bytes) -> None:
    def _put():
        _get_client().put_object(Bucket=bucket(), Key=key, Body=data, ContentType=content_type(key))
    await asyncio.to_thread(_wrap, _put)
    invalidate_cache()


async def make_folder(prefix: str) -> None:
    if not prefix.endswith("/"):
        prefix += "/"

    def _put():
        _get_client().put_object(Bucket=bucket(), Key=prefix, Body=b"")
    await asyncio.to_thread(_wrap, _put)
    invalidate_cache()


async def delete(key: str) -> None:
    def _del():
        _get_client().delete_object(Bucket=bucket(), Key=key)
    await asyncio.to_thread(_wrap, _del)
    invalidate_cache()


async def test_connection() -> dict:
    """Lists the root folder, then writes and deletes a small object."""
    def _test():
        client = _get_client()
        listing = _list_recursive_sync(root())
        probe = f"{root()}.parser-eval-write-test"
        client.put_object(Bucket=bucket(), Key=probe, Body=b"ok")
        client.delete_object(Bucket=bucket(), Key=probe)
        return {"files": len(listing)}
    return await asyncio.to_thread(_wrap, _test)
