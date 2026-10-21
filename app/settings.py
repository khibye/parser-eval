"""Settings edited in the browser and saved on the PVC. Secret fields are stored encrypted."""

import copy
from typing import Any

from . import storage

SETTINGS_FILE = "settings.json"

DEFAULTS: dict[str, dict[str, Any]] = {
    "s3": {
        "endpoint": "",
        "bucket": "",
        "root": "",
        "region": "us-east-1",
        "addressing": "path",
        "access_key": "",
        "secret_key": "",
        "verify_tls": True,
        "ca_pem": "",
    },
    "judge": {
        "url": "",
        "model": "",
        "api_key": "",
        "context_tokens": 500000,
        "parallel": 4,
        "vision": True,
        "temperature": 0,
        "timeout": 300,
    },
    "vlm": {
        "upstream": "",
        "key_mode": "passthrough",  # passthrough | saved
        "api_key": "",
        "timeout": 300,
    },
    "eval": {
        # Name of the tag docintel wraps image analysis in.
        "image_tag": "image_analysis",
        "change_threshold": 15,
        "fuzzy_threshold": 90,
    },
    "storage": {
        "keep_days": 0,  # 0 = keep until deleted
    },
}

SECRET_FIELDS = {("s3", "secret_key"), ("judge", "api_key"), ("vlm", "api_key")}

_cache: dict | None = None
_version = 0


def _path():
    return storage.data_path("config", SETTINGS_FILE)


def _load_raw() -> dict:
    global _cache
    if _cache is None:
        stored = storage.read_json(_path(), {}) or {}
        merged = copy.deepcopy(DEFAULTS)
        for section, values in stored.items():
            if section in merged and isinstance(values, dict):
                merged[section].update(values)
        _cache = merged
    return _cache


def version() -> int:
    """Changes every time settings are saved, so cached clients can be rebuilt."""
    return _version


def get(section: str) -> dict:
    """Section with secrets decrypted, for server-side use only."""
    raw = copy.deepcopy(_load_raw()[section])
    for sec, field in SECRET_FIELDS:
        if sec == section:
            raw[field] = storage.decrypt(raw.get(field))
    return raw


def public(section: str) -> dict:
    """Section for the browser: secrets replaced by whether they are set."""
    raw = copy.deepcopy(_load_raw()[section])
    for sec, field in SECRET_FIELDS:
        if sec == section:
            raw[field] = {"set": bool(storage.decrypt(raw.get(field)))}
    return raw


def all_public() -> dict:
    return {name: public(name) for name in DEFAULTS}


def update(section: str, values: dict) -> dict:
    """Merge values into a section. A secret is replaced only when a string is sent."""
    global _version
    if section not in DEFAULTS:
        raise KeyError(section)
    raw = _load_raw()
    current = raw[section]
    for key, value in values.items():
        if key not in DEFAULTS[section]:
            continue
        if (section, key) in SECRET_FIELDS:
            if isinstance(value, str):
                current[key] = storage.encrypt(value) if value else ""
            continue
        default = DEFAULTS[section][key]
        if isinstance(default, bool):
            value = bool(value)
        elif isinstance(default, int) and not isinstance(default, bool):
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
        elif isinstance(default, str):
            value = "" if value is None else str(value)
        current[key] = value
    storage.write_json(_path(), raw)
    _version += 1
    return public(section)


def ca_bundle_path() -> str | None:
    """Writes the pasted CA certificate to a file boto3 and httpx can use."""
    pem = _load_raw()["s3"].get("ca_pem") or ""
    if not pem.strip():
        return None
    path = storage.data_path("config", "ca.pem")
    if not path.exists() or path.read_text(encoding="utf-8") != pem:
        path.write_text(pem, encoding="utf-8")
    return str(path)
