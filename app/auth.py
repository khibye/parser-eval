"""One shared password, scrypt-hashed on the PVC, and signed session cookies."""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import HTTPException, Request

from . import config, storage

COOKIE = "pe_session"
AUTH_FILE = "auth.json"


def _auth_path():
    return storage.data_path("config", AUTH_FILE)


def _hash(password: str, salt: bytes) -> str:
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return base64.b64encode(digest).decode("ascii")


def load() -> dict:
    data = storage.read_json(_auth_path())
    if data is None:
        data = {"epoch": 1, "session_days": 7}
        if config.APP_INITIAL_PASSWORD:
            salt = os.urandom(16)
            data.update(salt=base64.b64encode(salt).decode(), hash=_hash(config.APP_INITIAL_PASSWORD, salt),
                        source="initial")
        storage.write_json(_auth_path(), data)
    return data


def check_password(password: str) -> bool:
    data = load()
    if "hash" not in data:
        return False
    salt = base64.b64decode(data["salt"])
    return hmac.compare_digest(_hash(password, salt), data["hash"])


def set_password(new_password: str) -> None:
    data = load()
    salt = os.urandom(16)
    data.update(salt=base64.b64encode(salt).decode(), hash=_hash(new_password, salt), source="user",
                changed=time.time())
    storage.write_json(_auth_path(), data)


def set_session_days(days: int) -> None:
    data = load()
    data["session_days"] = days
    storage.write_json(_auth_path(), data)


def sign_out_all() -> None:
    data = load()
    data["epoch"] = int(data.get("epoch", 1)) + 1
    storage.write_json(_auth_path(), data)


def make_cookie(remember: bool) -> tuple[str, int | None]:
    data = load()
    days = int(data.get("session_days", 7)) if remember else 1
    exp = int(time.time()) + days * 86400
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp, "epoch": data.get("epoch", 1)}).encode()).decode()
    sig = hmac.new(storage.signing_key(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}", (days * 86400 if remember else None)


def valid_cookie(value: str | None) -> bool:
    if not value or "." not in value:
        return False
    payload, sig = value.rsplit(".", 1)
    expected = hmac.new(storage.signing_key(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        body = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except Exception:
        return False
    return body.get("exp", 0) > time.time() and body.get("epoch") == load().get("epoch", 1)


async def require_auth(request: Request) -> None:
    if not valid_cookie(request.cookies.get(COOKIE)):
        raise HTTPException(status_code=401, detail="Sign in to continue.")


_failures: dict[str, float] = {}


async def slow_down(client: str) -> None:
    """Small delay after a wrong password, so guessing is slow."""
    last = _failures.get(client, 0)
    if time.time() - last < 60:
        await asyncio.sleep(1.5)
    _failures[client] = time.time()
