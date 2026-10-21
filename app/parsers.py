"""Parser definitions and the call that mimics Open WebUI's Docling loader."""

import json
import time
import uuid
from typing import Any

import httpx

from . import settings, storage

PARSERS_FILE = "parsers.json"
DOCLING_PATH = "/v1/convert/file"

DEFAULT_PARAMS = {"to_formats": ["md"], "image_export_mode": "placeholder", "do_ocr": True}


def _path():
    return storage.data_path("config", PARSERS_FILE)


def _load() -> list[dict]:
    return storage.read_json(_path(), []) or []


def _save(items: list[dict]) -> None:
    storage.write_json(_path(), items)


def _public(p: dict) -> dict:
    out = {k: v for k, v in p.items() if k != "token"}
    out["token"] = {"set": bool(storage.decrypt(p.get("token")))}
    return out


def list_public() -> list[dict]:
    return [_public(p) for p in _load()]


def get(parser_id: str) -> dict | None:
    for p in _load():
        if p["id"] == parser_id:
            return p
    return None


def _clean(values: dict, existing: dict | None) -> dict:
    p = dict(existing or {})
    for field in ("name", "description", "url", "auth_type", "header_name", "text_field", "list_mode"):
        if field in values and values[field] is not None:
            p[field] = str(values[field]).strip()
    for field, default in (("timeout", 600), ("parallel", 8)):
        if field in values:
            try:
                p[field] = max(1, int(values[field]))
            except (TypeError, ValueError):
                p[field] = default
    if "params" in values:
        params = values["params"]
        if isinstance(params, str):
            params = json.loads(params) if params.strip() else {}
        if not isinstance(params, dict):
            raise ValueError("Docling parameters must be a JSON object.")
        p["params"] = params
    if isinstance(values.get("token"), str):
        p["token"] = storage.encrypt(values["token"]) if values["token"] else ""
    p.setdefault("auth_type", "none")
    p.setdefault("header_name", "Authorization")
    p.setdefault("text_field", "document.md_content")
    p.setdefault("list_mode", "repeat")
    p.setdefault("timeout", 600)
    p.setdefault("parallel", 8)
    p.setdefault("params", dict(DEFAULT_PARAMS))
    p.setdefault("description", "")
    p["url"] = p.get("url", "").rstrip("/")
    if not p.get("name"):
        raise ValueError("Give the parser a name.")
    if not p.get("url"):
        raise ValueError("Give the parser a docintel URL.")
    return p


def create(values: dict) -> dict:
    items = _load()
    p = _clean(values, None)
    if any(i["name"] == p["name"] for i in items):
        raise ValueError(f"A parser named {p['name']} already exists.")
    p["id"] = uuid.uuid4().hex[:10]
    p["created"] = time.time()
    items.append(p)
    _save(items)
    return _public(p)


def update(parser_id: str, values: dict) -> dict:
    items = _load()
    for idx, item in enumerate(items):
        if item["id"] == parser_id:
            p = _clean(values, item)
            if any(i["name"] == p["name"] and i["id"] != parser_id for i in items):
                raise ValueError(f"A parser named {p['name']} already exists.")
            items[idx] = p
            _save(items)
            return _public(p)
    raise KeyError(parser_id)


def delete(parser_id: str) -> None:
    _save([p for p in _load() if p["id"] != parser_id])


def snapshot(p: dict) -> dict:
    """What a run keeps about the parser, without the token."""
    return {k: v for k, v in p.items() if k != "token"}


def _headers(p: dict) -> dict:
    token = storage.decrypt(p.get("token"))
    if not token or p.get("auth_type") == "none":
        return {}
    if p.get("auth_type") == "bearer":
        return {"Authorization": f"Bearer {token}"}
    return {p.get("header_name") or "Authorization": token}


def _form_fields(p: dict) -> dict[str, str | list[str]]:
    """Docling parameters as multipart form fields. A list becomes repeated fields or one JSON string."""
    fields: dict[str, str | list[str]] = {}
    for key, value in (p.get("params") or {}).items():
        if isinstance(value, bool):
            fields[key] = "true" if value else "false"
        elif isinstance(value, list) and p.get("list_mode", "repeat") == "repeat":
            fields[key] = [item if isinstance(item, str) else json.dumps(item) for item in value]
        elif isinstance(value, (list, dict)):
            fields[key] = json.dumps(value)
        elif value is None:
            continue
        else:
            fields[key] = str(value)
    return fields


def extract_text(body: Any, field_path: str) -> str | None:
    node = body
    for part in (field_path or "").split("."):
        if not part:
            continue
        if isinstance(node, list):
            node = node[0] if node else None
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    if isinstance(node, str):
        return node
    return None


def _verify():
    return settings.ca_bundle_path() or True


async def call(p: dict, filename: str, data: bytes, ctype: str) -> dict:
    """Sends one file the way Open WebUI sends it to Docling and returns the text."""
    url = p["url"] + DOCLING_PATH
    started = time.perf_counter()
    result: dict[str, Any] = {"ok": False, "text": "", "status": None, "error": None, "size": 0}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(p.get("timeout", 600), connect=15),
                                     verify=_verify()) as client:
            resp = await client.post(url, headers=_headers(p), data=_form_fields(p),
                                     files={"files": (filename, data, ctype)})
        result["status"] = resp.status_code
        result["size"] = len(resp.content)
        if resp.status_code >= 400:
            result["error"] = f"HTTP {resp.status_code}: {resp.text[:500]}"
        else:
            try:
                body = resp.json()
            except ValueError:
                result["error"] = "The response is not JSON."
            else:
                text = extract_text(body, p.get("text_field", "document.md_content"))
                if text is None:
                    result["error"] = f"No text at {p.get('text_field')} in the response."
                else:
                    result["ok"] = True
                    result["text"] = text
    except httpx.TimeoutException:
        result["error"] = f"Timed out after {p.get('timeout', 600)} s."
    except httpx.HTTPError as exc:
        result["error"] = f"Request failed: {exc.__class__.__name__}: {exc}"
    result["elapsed"] = round(time.perf_counter() - started, 3)
    return result


async def health(p: dict) -> dict:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=5, verify=_verify()) as client:
            await client.get(p["url"] + "/health", headers=_headers(p))
        return {"ok": True, "ms": round((time.perf_counter() - started) * 1000)}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc) or exc.__class__.__name__}
