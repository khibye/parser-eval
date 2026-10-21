"""Caching proxy between docintel and LiteLLM for VLM calls.

docintel's VLM URL points here (port 8081, not exposed by the Route). Answers are stored by the image
pixels, the prompt and the model, so two parser versions that send the same image get the same answer
and the comparison shows only what the parser changed.
"""

import base64
import hashlib
import io
import json
import time
import uuid

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from PIL import Image

from . import settings, storage

MODES = ("replay", "live", "refresh")


class ProxyState:
    def __init__(self) -> None:
        self.mode = "replay"
        self.owner: str | None = None  # run id that set the mode
        self.calls = 0
        self.hits = 0
        self.errors = 0
        self.last_call: float | None = None

    def snapshot(self) -> dict:
        return {"mode": self.mode, "owner": self.owner, "calls": self.calls, "hits": self.hits,
                "errors": self.errors, "last_call": self.last_call}


state = ProxyState()


def _pixel_hash(data_url: str) -> str:
    try:
        b64 = data_url.split(",", 1)[1]
        raw = base64.b64decode(b64)
    except Exception:
        return hashlib.sha256(data_url.encode()).hexdigest()
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
        rgba = img.convert("RGBA")
        h = hashlib.sha256(f"{rgba.size}".encode())
        h.update(rgba.tobytes())
        return "px:" + h.hexdigest()
    except Exception:
        return "raw:" + hashlib.sha256(raw).hexdigest()


def _canonical(node):
    """The request with every inline image replaced by a hash of its pixels."""
    if isinstance(node, dict):
        if node.get("type") == "image_url":
            url = node.get("image_url", {})
            url = url.get("url", "") if isinstance(url, dict) else str(url)
            return {"type": "image", "hash": _pixel_hash(url) if url.startswith("data:") else url}
        return {k: _canonical(v) for k, v in sorted(node.items())}
    if isinstance(node, list):
        return [_canonical(v) for v in node]
    return node


def cache_key(body: dict) -> str:
    material = {k: body.get(k) for k in ("model", "temperature", "max_tokens", "response_format", "top_p")}
    material["messages"] = _canonical(body.get("messages", []))
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _cache_path(key: str):
    return storage.data_path("vlm-cache", key[:2], f"{key}.json")


def cache_stats() -> dict:
    folder = storage.data_path("vlm-cache")
    count, size = 0, 0
    if folder.exists():
        for path in folder.rglob("*.json"):
            count += 1
            size += path.stat().st_size
    return {"answers": count, "bytes": size}


def clear_cache() -> int:
    folder = storage.data_path("vlm-cache")
    removed = 0
    if folder.exists():
        for path in folder.rglob("*.json"):
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def _upstream_headers(request: Request) -> dict:
    cfg = settings.get("vlm")
    if cfg["key_mode"] == "saved" and cfg["api_key"]:
        return {"Authorization": f"Bearer {cfg['api_key']}"}
    auth = request.headers.get("authorization")
    return {"Authorization": auth} if auth else {}


def _sse(response: dict):
    """Turns a full chat completion into a stream, for clients that asked for one."""
    choice = (response.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content") or ""
    base = {"id": response.get("id", f"chatcmpl-{uuid.uuid4().hex}"), "object": "chat.completion.chunk",
            "created": response.get("created", int(time.time())), "model": response.get("model", "")}
    chunks = [
        {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}]},
        {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": choice.get("finish_reason", "stop")}]},
    ]

    async def gen():
        for chunk in chunks:
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    return gen()


proxy_app = FastAPI(title="parser-eval VLM proxy", docs_url=None, redoc_url=None, openapi_url=None)


@proxy_app.get("/health")
async def health():
    return {"ok": True, **state.snapshot()}


async def _chat(request: Request):
    body = await request.json()
    wants_stream = bool(body.get("stream"))
    key = cache_key(body)
    path = _cache_path(key)
    state.calls += 1
    state.last_call = time.time()

    if state.mode == "replay":
        cached = storage.read_json(path)
        if cached:
            state.hits += 1
            response = cached["response"]
            return StreamingResponse(_sse(response), media_type="text/event-stream") if wants_stream \
                else JSONResponse(response)

    upstream = settings.get("vlm")["upstream"].rstrip("/")
    if not upstream:
        state.errors += 1
        return JSONResponse({"error": {"message": "parser-eval: the VLM upstream URL is not set in Settings."}},
                            status_code=502)
    forward = dict(body)
    forward["stream"] = False
    forward.pop("stream_options", None)
    try:
        async with httpx.AsyncClient(timeout=float(settings.get("vlm")["timeout"]),
                                     verify=settings.ca_bundle_path() or True) as client:
            resp = await client.post(f"{upstream}/chat/completions", json=forward, headers=_upstream_headers(request))
    except httpx.HTTPError as exc:
        state.errors += 1
        return JSONResponse({"error": {"message": f"parser-eval: upstream failed: {exc}"}}, status_code=502)
    if resp.status_code >= 400:
        state.errors += 1
        return Response(resp.content, status_code=resp.status_code,
                        media_type=resp.headers.get("content-type", "application/json"))
    response = resp.json()
    if state.mode == "refresh" or not path.exists():
        storage.write_json(path, {"key": key, "model": body.get("model"), "stored": time.time(), "response": response})
    if wants_stream:
        return StreamingResponse(_sse(response), media_type="text/event-stream")
    return JSONResponse(response)


@proxy_app.post("/v1/chat/completions")
async def chat_v1(request: Request):
    return await _chat(request)


@proxy_app.post("/chat/completions")
async def chat_plain(request: Request):
    return await _chat(request)


@proxy_app.api_route("/{path:path}", methods=["GET", "POST"])
async def passthrough(path: str, request: Request):
    """Anything else (for example /v1/models) goes to LiteLLM unchanged."""
    upstream = settings.get("vlm")["upstream"].rstrip("/")
    if not upstream:
        return JSONResponse({"error": {"message": "parser-eval: the VLM upstream URL is not set."}}, status_code=502)
    sub = path[3:] if path.startswith("v1/") else path
    async with httpx.AsyncClient(timeout=60, verify=settings.ca_bundle_path() or True) as client:
        resp = await client.request(request.method, f"{upstream}/{sub}", content=await request.body(),
                                    headers={**_upstream_headers(request),
                                             "content-type": request.headers.get("content-type", "application/json")})
    return Response(resp.content, status_code=resp.status_code,
                    media_type=resp.headers.get("content-type", "application/json"))
