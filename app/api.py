"""HTTP API used by the browser UI. Everything except sign-in needs the session cookie."""

import io
import json
import shutil
import time
import uuid
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from PIL import Image

from . import (auth, checks, compare, config, llm, parsers, report, runner, s3store, samples, settings, storage,
               vlm_eval)
from .vlm_proxy import cache_stats, clear_cache, state as proxy_state

public = APIRouter(prefix="/api")
api = APIRouter(prefix="/api", dependencies=[Depends(auth.require_auth)])


# ---------- auth ----------

@public.post("/login")
async def login(request: Request):
    body = await request.json()
    if not auth.check_password(str(body.get("password", ""))):
        await auth.slow_down(request.client.host if request.client else "?")
        raise HTTPException(401, "That password is not right.")
    value, max_age = auth.make_cookie(bool(body.get("remember", True)))
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.COOKIE, value, max_age=max_age, httponly=True, samesite="lax", secure=config.COOKIE_SECURE)
    return resp


@public.post("/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


@public.get("/status")
async def status(request: Request):
    data = auth.load()
    return {"signed_in": auth.valid_cookie(request.cookies.get(auth.COOKIE)),
            "password_set": "hash" in data}


@api.get("/me")
async def me():
    data = auth.load()
    return {"ok": True, "session_days": data.get("session_days", 7), "password_source": data.get("source")}


@api.post("/auth/password")
async def change_password(request: Request):
    body = await request.json()
    if not auth.check_password(str(body.get("current", ""))):
        raise HTTPException(400, "The current password is not right.")
    new = str(body.get("new", ""))
    if len(new) < 8:
        raise HTTPException(400, "Use at least 8 characters for the new password.")
    auth.set_password(new)
    return {"ok": True}


@api.post("/auth/session")
async def session_days(request: Request):
    body = await request.json()
    days = int(body.get("days", 7))
    if days not in (1, 7, 30):
        raise HTTPException(400, "Choose 1, 7 or 30 days.")
    auth.set_session_days(days)
    return {"ok": True}


@api.post("/auth/signout-all")
async def signout_all():
    auth.sign_out_all()
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


# ---------- settings ----------

@api.get("/settings")
async def get_settings():
    data = settings.all_public()
    data["proxy_url"] = config.VLM_PROXY_PUBLIC_URL
    data["session_days"] = auth.load().get("session_days", 7)
    return data


@api.put("/settings/{section}")
async def put_settings(section: str, request: Request):
    try:
        return settings.update(section, await request.json())
    except KeyError:
        raise HTTPException(404, "Unknown settings section.")


@api.post("/settings/test/s3")
async def test_s3():
    s3store.invalidate_cache()
    return await s3store.test_connection()


@api.post("/settings/test/judge")
async def test_judge():
    cfg = settings.get("judge")
    res = await llm.chat(cfg["url"], cfg["api_key"], cfg["model"],
                         [{"role": "user", "content": "Reply with the single word OK."}], max_tokens=10, timeout=60)
    out = {"ok": True, "elapsed": res["elapsed"], "reply": res["content"][:50], "vision": None}
    if cfg["vision"]:
        img = Image.new("RGB", (64, 32), "white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        try:
            await llm.chat(cfg["url"], cfg["api_key"], cfg["model"], [{"role": "user", "content": [
                {"type": "text", "text": "What color is this image? One word."},
                llm.image_part(buf.getvalue())]}], max_tokens=10, timeout=60)
            out["vision"] = True
        except llm.LLMError as exc:
            out["vision"] = False
            out["vision_error"] = str(exc)
    return out


@api.post("/settings/test/vlm")
async def test_vlm(request: Request):
    body = await request.json()
    url, key = settings.get("vlm")["upstream"], settings.get("vlm")["api_key"] or settings.get("judge")["api_key"]
    res = await llm.chat(url, key, body.get("model", ""), [{"role": "user", "content": "Reply with OK."}],
                         max_tokens=10, timeout=60)
    return {"ok": True, "elapsed": res["elapsed"]}


@api.get("/storage")
async def storage_usage():
    def size(path):
        total = 0
        if path.exists():
            for p in path.rglob("*"):
                if p.is_file():
                    total += p.stat().st_size
        return total
    usage = shutil.disk_usage(config.DATA_DIR)
    return {"total": usage.total, "used": usage.used, "free": usage.free,
            "parts": {"runs": size(storage.data_path("runs")), "vlm_cache": size(storage.data_path("vlm-cache")),
                      "vlm_runs": size(storage.data_path("vlm-runs")),
                      "checks_settings": size(storage.data_path("checks")) + size(storage.data_path("config")),
                      "outputs": size(storage.data_path("outputs"))},
            "runs": len(runner.list_runs())}


@api.post("/storage/cleanup")
async def storage_cleanup():
    return {"removed": runner.cleanup_old_runs()}


@api.get("/vlm-proxy/status")
async def proxy_status():
    return {**proxy_state.snapshot(), **cache_stats(), "url": config.VLM_PROXY_PUBLIC_URL}


@api.post("/vlm-cache/clear")
async def vlm_cache_clear():
    return {"removed": clear_cache()}


# ---------- files ----------

def _last_results() -> dict:
    return storage.read_json(storage.data_path("outputs", "last-results.json"), {}) or {}


@api.get("/files")
async def files(prefix: str | None = None):
    root = s3store.root()
    prefix = root if prefix is None else prefix
    if not prefix.startswith(root):
        prefix = root
    listing = await s3store.list_folder(prefix)
    all_checks = checks.all_by_fid()
    last = _last_results()
    out = []
    for f in listing["files"]:
        fid = storage.file_id(f["key"])
        c = all_checks.get(fid) or {}
        items = c.get("checks", [])
        out.append({**f, "fid": fid, "name": PurePosixPath(f["key"]).name, "type": s3store.file_type(f["key"]),
                    "approved": sum(1 for x in items if x.get("status") == "approved"),
                    "drafts": sum(1 for x in items if x.get("status") == "draft"),
                    "changed": bool(items) and c.get("etag") not in (None, f["etag"]),
                    "last": last.get(fid)})
    folders = [{"prefix": p, "name": p[len(prefix):]} for p in listing["folders"]]
    return {"prefix": prefix, "root": root, "folders": folders, "files": out}


@api.get("/datasets")
async def datasets():
    root = s3store.root()
    all_files = await s3store.list_recursive(root, max_age=30)
    folders: dict[str, dict] = {root: {"prefix": root, "count": 0, "types": {}}}
    for f in all_files:
        parts = f["key"][len(root):].split("/")[:-1]
        prefixes = [root] + [root + "/".join(parts[:i + 1]) + "/" for i in range(len(parts))]
        ftype = s3store.file_type(f["key"])
        for p in prefixes:
            d = folders.setdefault(p, {"prefix": p, "count": 0, "types": {}})
            d["count"] += 1
            d["types"][ftype] = d["types"].get(ftype, 0) + 1
    return sorted(folders.values(), key=lambda d: d["prefix"])


@api.post("/files/upload")
async def upload(prefix: str = Form(...), files: list[UploadFile] = File(...)):
    root = s3store.root()
    if not prefix.startswith(root):
        raise HTTPException(400, "Uploads must go inside the root folder.")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    saved = []
    for f in files:
        name = PurePosixPath(f.filename or "file").name
        key = prefix + name
        await s3store.put(key, await f.read())
        saved.append(key)
    return {"saved": saved}


@api.post("/files/folder")
async def new_folder(request: Request):
    body = await request.json()
    name = str(body.get("name", "")).strip().strip("/")
    if not name or "/" in name:
        raise HTTPException(400, "Use a folder name without slashes.")
    prefix = body.get("prefix") or s3store.root()
    await s3store.make_folder(prefix + name + "/")
    return {"prefix": prefix + name + "/"}


@api.delete("/files")
async def delete_file(key: str):
    await s3store.delete(key)
    return {"ok": True}


@api.get("/files/raw")
async def raw_file(key: str):
    data = await s3store.get_bytes(key)
    name = PurePosixPath(key).name
    return Response(data, media_type=s3store.content_type(key),
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_quote(name)}"})


def _quote(name: str) -> str:
    from urllib.parse import quote
    return quote(name)


@api.get("/files/preview")
async def preview(key: str, page: int = 1):
    """A PNG of one PDF page or of an image, for the test editor."""
    data = await s3store.get_bytes(key)
    ftype = s3store.file_type(key)
    pages = 1
    if PurePosixPath(key).suffix.lower() == ".pdf":
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(data)
        pages = len(pdf)
        idx = max(0, min(page - 1, pages - 1))
        img = pdf[idx].render(scale=1.5).to_pil()
    elif ftype == "Images":
        img = Image.open(io.BytesIO(data))
        img.load()
    else:
        raise HTTPException(404, "No preview for this file type. Download the file to look at it.")
    img = img.convert("RGB")
    img.thumbnail((1400, 1800))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"X-Pages": str(pages)})


# ---------- checks and outputs for one file ----------

@api.get("/checks")
async def get_checks(key: str):
    data = checks.load(key)
    meta = await s3store.head(key)
    current = meta["etag"] if meta else None
    return {**data, "current_etag": current, "exists": meta is not None,
            "changed": bool(data["checks"]) and data.get("etag") not in (None, current),
            "type": s3store.file_type(key), "size": meta["size"] if meta else None}


@api.put("/checks")
async def put_checks(request: Request):
    body = await request.json()
    key = body["key"]
    meta = await s3store.head(key)
    try:
        return checks.save(key, meta["etag"] if meta else None, body.get("checks", []))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def _outputs_for(fid: str) -> list[dict]:
    out = []
    folder = storage.data_path("outputs", fid)
    if folder.exists():
        for path in folder.glob("*.json"):
            d = storage.read_json(path) or {}
            out.append({"id": f"now:{path.stem}", "label": f"{d.get('parser_name', path.stem)} · parsed now",
                        "at": d.get("at", 0)})
    for run in runner.list_runs():
        for p in run.get("parsers") or []:
            if runner.result_path(run["id"], p["id"], fid).exists():
                out.append({"id": f"run:{run['id']}:{p['id']}", "label": f"{p['name']} · {run['name']}",
                            "at": run.get("created") or 0})
    out.sort(key=lambda o: o["at"], reverse=True)
    return out


@api.get("/file-outputs")
async def file_outputs(key: str):
    return _outputs_for(storage.file_id(key))


@api.get("/file-output")
async def file_output(key: str, id: str):
    fid = storage.file_id(key)
    if id.startswith("now:"):
        d = storage.read_json(storage.data_path("outputs", fid, f"{id[4:]}.json"))
    elif id.startswith("run:"):
        _, run_id, pid = id.split(":", 2)
        d = storage.read_json(runner.result_path(run_id, pid, fid))
    else:
        d = None
    if not d:
        raise HTTPException(404, "That output is not available.")
    return {"text": d.get("text", ""), "ok": d.get("ok", True), "error": d.get("error")}


@api.post("/parse-now")
async def parse_now(request: Request):
    body = await request.json()
    key, pid = body["key"], body["parser_id"]
    p = parsers.get(pid)
    if not p:
        raise HTTPException(404, "That parser does not exist.")
    data = await s3store.get_bytes(key)
    res = await parsers.call(p, PurePosixPath(key).name, data, s3store.content_type(key))
    fid = storage.file_id(key)
    storage.write_json(storage.data_path("outputs", fid, f"{pid}.json"),
                       {"text": res["text"], "ok": res["ok"], "error": res["error"], "at": time.time(),
                        "parser_name": p["name"], "elapsed": res["elapsed"]})
    return {"id": f"now:{pid}", **res}


@api.post("/checks/evaluate")
async def evaluate_checks(request: Request):
    body = await request.json()
    ev = settings.get("eval")
    out = {}
    for c in body.get("checks", []):
        if c.get("kind") == "question":
            out[c.get("id")] = {"passed": None, "detail": "answered by the judge during runs"}
        else:
            out[c.get("id")] = checks.evaluate(c, body.get("text", ""), image_tag=ev["image_tag"],
                                               fuzzy_threshold=int(ev["fuzzy_threshold"]))
    return out


@api.post("/checks/suggest")
async def suggest(request: Request):
    body = await request.json()
    key, text = body["key"], body.get("text", "")
    if not text.strip():
        raise HTTPException(400, "Pick an output first, so the judge has text to read.")
    proposed = await llm.suggest_checks(text, PurePosixPath(key).name)
    data = checks.load(key)
    drafts = []
    for c in proposed:
        try:
            item = checks.clean_check({**c, "status": "draft", "source": "judge"})
        except (ValueError, TypeError):
            continue
        drafts.append(item)
    meta = await s3store.head(key)
    saved = checks.save(key, meta["etag"] if meta else data.get("etag"), data["checks"] + drafts)
    return {"added": len(drafts), **saved}


# ---------- parsers ----------

@api.get("/parsers")
async def list_parsers():
    return parsers.list_public()


@api.post("/parsers")
async def create_parser(request: Request):
    try:
        return parsers.create(await request.json())
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc))


@api.put("/parsers/{pid}")
async def update_parser(pid: str, request: Request):
    try:
        return parsers.update(pid, await request.json())
    except KeyError:
        raise HTTPException(404, "That parser does not exist.")
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc))


@api.delete("/parsers/{pid}")
async def delete_parser(pid: str):
    parsers.delete(pid)
    return {"ok": True}


@api.post("/parsers/{pid}/duplicate")
async def duplicate_parser(pid: str):
    p = parsers.get(pid)
    if not p:
        raise HTTPException(404, "That parser does not exist.")
    copy = {k: v for k, v in p.items() if k not in ("id", "created", "token")}
    copy["name"] = f"{p['name']}-copy-{uuid.uuid4().hex[:3]}"
    created = parsers.create(copy)
    items = parsers._load()
    for item in items:
        if item["id"] == created["id"]:
            item["token"] = p.get("token", "")
    parsers._save(items)
    return created


@api.post("/parsers/{pid}/test")
async def test_parser(pid: str):
    p = parsers.get(pid)
    if not p:
        raise HTTPException(404, "That parser does not exist.")
    probe = config.SAMPLES_DIR / "probe.pdf"
    res = await parsers.call(p, "probe.pdf", probe.read_bytes(), "application/pdf")
    return {"ok": res["ok"], "status": res["status"], "elapsed": res["elapsed"], "chars": len(res["text"]),
            "error": res["error"], "preview": res["text"][:300]}


@api.get("/parsers/health")
async def parsers_health():
    import asyncio
    items = parsers._load()
    results = await asyncio.gather(*(parsers.health(p) for p in items))
    return {p["id"]: r for p, r in zip(items, results)}


# ---------- runs ----------

@api.get("/runs")
async def list_runs():
    return {"runs": runner.list_runs(), "current": runner.queue.current, "queued": runner.queue.pending()}


@api.post("/runs/estimate")
async def estimate(request: Request):
    return await runner.estimate(await request.json())


@api.post("/runs")
async def create_run(request: Request):
    try:
        run = await runner.create_run(await request.json())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": run["id"]}


def _run_or_404(run_id: str) -> dict:
    run = runner.load_run(run_id)
    if not run:
        raise HTTPException(404, "That run does not exist.")
    return run


@api.get("/runs/{run_id}")
async def get_run(run_id: str):
    return _run_or_404(run_id)


@api.post("/runs/{run_id}/resume")
async def resume_run(run_id: str):
    _run_or_404(run_id)
    await runner.resume(run_id)
    return {"ok": True}


@api.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    _run_or_404(run_id)
    runner.cancel(run_id)
    return {"ok": True}


@api.delete("/runs/{run_id}")
async def delete_run(run_id: str):
    runner.delete_run(run_id)
    return {"ok": True}


@api.get("/runs/{run_id}/compare")
async def compare_run(run_id: str, a: str, b: str):
    run = _run_or_404(run_id)
    return compare.summary(run, a, b)


@api.get("/runs/{run_id}/single")
async def single_run(run_id: str, pid: str):
    run = _run_or_404(run_id)
    rows = []
    for f in run["files"]:
        r = storage.read_json(runner.result_path(run_id, pid, f["fid"]))
        rows.append({"fid": f["fid"], "key": f["key"], "type": f["type"],
                     **({k: r[k] for k in ("ok", "error", "elapsed", "passed", "evaluated", "image_blocks", "chars")}
                        if r else {"pending": True})})
    return {"summary": compare.single_summary(run, pid), "rows": rows}


@api.get("/runs/{run_id}/files/{fid}")
async def run_file(run_id: str, fid: str, a: str, b: str | None = None, only_changes: bool = True):
    run = _run_or_404(run_id)
    try:
        return compare.file_detail(run, a, b, fid, only_changes)
    except KeyError:
        raise HTTPException(404, "That file is not part of the run.")


@api.get("/runs/{run_id}/report")
async def run_report(run_id: str, a: str, b: str):
    run = _run_or_404(run_id)
    html = report.build(run, a, b)
    return HTMLResponse(html, headers={"Content-Disposition": f"attachment; filename=\"{run_id}-report.html\""})


@api.get("/runs/{run_id}/output/{pid}/{fid}")
async def run_output(run_id: str, pid: str, fid: str):
    r = storage.read_json(runner.result_path(run_id, pid, fid))
    if not r:
        raise HTTPException(404, "No output for that file yet.")
    return PlainTextResponse(r.get("text", ""))


# ---------- presets ----------

def _presets_path():
    return storage.data_path("config", "presets.json")


@api.get("/presets")
async def get_presets():
    return storage.read_json(_presets_path(), []) or []


@api.post("/presets")
async def save_preset(request: Request):
    body = await request.json()
    items = storage.read_json(_presets_path(), []) or []
    name = str(body.get("name") or body.get("config", {}).get("name") or "Preset").strip()
    items = [i for i in items if i["name"] != name] + [{"name": name, "config": body.get("config", {})}]
    storage.write_json(_presets_path(), items)
    return items


@api.delete("/presets")
async def delete_preset(name: str):
    items = [i for i in (storage.read_json(_presets_path(), []) or []) if i["name"] != name]
    storage.write_json(_presets_path(), items)
    return items


# ---------- VLM quality ----------

@api.get("/vlm-runs")
async def vlm_runs():
    return vlm_eval.list_runs()


@api.post("/vlm-runs")
async def create_vlm_run(request: Request):
    try:
        run = await vlm_eval.create(await request.json())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": run["id"]}


@api.get("/vlm-runs/{run_id}")
async def get_vlm_run(run_id: str):
    run = vlm_eval.load(run_id)
    if not run:
        raise HTTPException(404, "That VLM run does not exist.")
    return {"run": {k: v for k, v in run.items() if k != "images"}, "summary": vlm_eval.summary(run)}


@api.get("/vlm-runs/{run_id}/images/{index}")
async def vlm_image_detail(run_id: str, index: int):
    run = vlm_eval.load(run_id)
    if not run:
        raise HTTPException(404, "That VLM run does not exist.")
    return vlm_eval.image_detail(run, index)


@api.get("/vlm-runs/{run_id}/images/{index}/bytes")
async def vlm_image_bytes(run_id: str, index: int):
    run = vlm_eval.load(run_id)
    if not run or not (0 <= index < len(run["images"])):
        raise HTTPException(404, "Not found.")
    key = run["images"][index]["key"]
    return Response(await s3store.get_bytes(key), media_type=s3store.content_type(key))


@api.post("/vlm-runs/{run_id}/cancel")
async def cancel_vlm(run_id: str):
    runner.queue.cancelled.add(run_id)
    return {"ok": True}


@api.delete("/vlm-runs/{run_id}")
async def delete_vlm(run_id: str):
    runner.queue.cancelled.add(run_id)
    vlm_eval.delete(run_id)
    return {"ok": True}


@api.get("/vlm-default-prompt")
async def vlm_default_prompt():
    return {"prompt": vlm_eval.DEFAULT_PROMPT}


# ---------- samples ----------

@api.get("/samples")
async def list_samples():
    return samples.manifest()


@api.post("/samples/import")
async def import_samples(request: Request):
    body = await request.json()
    prefix = body.get("prefix") or (s3store.root() + "synthetic/")
    return await samples.import_to_s3(prefix)
