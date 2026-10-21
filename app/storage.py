"""JSON files on the PVC, written atomically, plus encryption of stored secrets."""

import base64
import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from . import config

_lock = threading.RLock()


def data_path(*parts: str) -> Path:
    return config.DATA_DIR.joinpath(*parts)


def ensure_dirs() -> None:
    for sub in ("config", "checks", "runs", "vlm-cache", "vlm-runs", "outputs"):
        data_path(sub).mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        # A half-written file cannot happen with write_json, but a hand edit can.
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with _lock:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _derived(label: bytes) -> bytes:
    secret = config.APP_SECRET_KEY.encode("utf-8")
    if not secret:
        # Development fallback. The Helm chart always provides a real key.
        secret = b"parser-eval-dev-only-key"
    return hashlib.sha256(label + b":" + secret).digest()


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_derived(b"encrypt")))


def signing_key() -> bytes:
    return _derived(b"session")


def encrypt(value: str) -> dict:
    return {"enc": _fernet().encrypt(value.encode("utf-8")).decode("ascii")}


def decrypt(value: Any) -> str:
    if isinstance(value, dict) and "enc" in value:
        try:
            return _fernet().decrypt(value["enc"].encode("ascii")).decode("utf-8")
        except InvalidToken:
            return ""
    if isinstance(value, str):
        return value
    return ""


def file_id(key: str) -> str:
    """Stable short id for an S3 key, safe to use in paths and URLs."""
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
