"""VLM quality: the same images through several VLM setups, scored by the judge looking at each image."""

import asyncio
import logging
import statistics
import time
import traceback
import uuid
from pathlib import PurePosixPath

from . import llm, s3store, settings, storage

log = logging.getLogger("parser-eval.vlm")

DEFAULT_PROMPT = (
    "Describe this image for a search index. Transcribe all text exactly, in its original language. "
    "For charts and tables, give every label and value. Do not guess what you cannot see."
)
CRITERIA = ("accurate", "complete", "text", "no_invention", "overall")


def vdir(run_id: str):
    return storage.data_path("vlm-runs", run_id)


def load(run_id: str) -> dict | None:
    return storage.read_json(vdir(run_id) / "run.json")


def save(run: dict) -> None:
    storage.write_json(vdir(run["id"]) / "run.json", run)


def list_runs() -> list[dict]:
    out = []
    folder = storage.data_path("vlm-runs")
    if folder.exists():
        for sub in folder.iterdir():
            run = storage.read_json(sub / "run.json")
            if run:
                out.append({k: run.get(k) for k in ("id", "name", "created", "status", "prefix", "setups",
                                                      "progress", "image_count", "error")})
    out.sort(key=lambda r: r.get("created") or 0, reverse=True)
    return out


async def create(body: dict) -> dict:
    from .runner import queue
    prefix = body.get("prefix") or s3store.root()
    files = [f for f in await s3store.list_recursive(prefix) if s3store.file_type(f["key"]) == "Images"]
    if not files:
        raise ValueError("No images in that folder.")
    setups = []
    for s in body.get("setups", []):
        if not s.get("model"):
            continue
        setups.append({"name": s.get("name") or s["model"], "model": s["model"],
                       "prompt": s.get("prompt") or DEFAULT_PROMPT,
                       "max_side": int(s.get("max_side") or 1600), "flatten": bool(s.get("flatten", True))})
    if len(setups) < 1:
        raise ValueError("Add at least one VLM setup with a model name.")
    images = []
    for f in sorted(files, key=lambda x: x["key"]):
        rel = f["key"][len(prefix):] if f["key"].startswith(prefix) else f["key"]
        kind = rel.split("/")[0] if "/" in rel else "(top folder)"
        images.append({"iid": storage.file_id(f["key"]), "key": f["key"], "kind": kind})
    run_id = time.strftime("vlm-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    run = {"id": run_id, "name": body.get("name") or f"{len(setups)} VLM setups", "created": time.time(),
           "status": "queued", "prefix": prefix, "setups": setups, "images": images, "image_count": len(images),
           "concurrency": max(1, int(body.get("concurrency") or 4)), "judge": bool(body.get("judge", True)),
           "progress": {"done": 0, "total": len(images) * len(setups), "scored": 0}, "error": None}
    save(run)
    await queue.put(("vlm", run_id))
    return run


def _out_path(run_id, si, iid):
    return vdir(run_id) / "out" / str(si) / f"{iid}.json"


def _score_path(run_id, si, iid):
    return vdir(run_id) / "scores" / str(si) / f"{iid}.json"


def _vlm_credentials() -> tuple[str, str]:
    vlm_cfg, judge_cfg = settings.get("vlm"), settings.get("judge")
    return vlm_cfg["upstream"], vlm_cfg["api_key"] or judge_cfg["api_key"]


async def execute(run_id: str) -> None:
    from .runner import queue
    run = load(run_id)
    if not run:
        return
    run["status"] = "running"
    run["started"] = run.get("started") or time.time()
    save(run)
    url, key = _vlm_credentials()
    sem = asyncio.Semaphore(run["concurrency"])
    lock = asyncio.Lock()
    cache: dict[str, bytes] = {}

    async def image(item) -> bytes:
        if item["iid"] not in cache:
            cache[item["iid"]] = await s3store.get_bytes(item["key"])
        return cache[item["iid"]]

    async def bump(field):
        async with lock:
            run["progress"][field] += 1
            save(run)

    async def one(si, setup, item):
        if run_id in queue.cancelled:
            return
        out = _out_path(run_id, si, item["iid"])
        if not out.exists():
            async with sem:
                try:
                    data = await image(item)
                    res = await llm.chat(url, key, setup["model"], [{"role": "user", "content": [
                        {"type": "text", "text": setup["prompt"]},
                        llm.image_part(data, setup["max_side"], setup["flatten"])]}],
                        max_tokens=1500, timeout=float(settings.get("vlm")["timeout"]))
                    storage.write_json(out, {"description": res["content"], "elapsed": res["elapsed"], "error": None})
                except (llm.LLMError, s3store.S3Error) as exc:
                    storage.write_json(out, {"description": "", "elapsed": None, "error": str(exc)})
            await bump("done")
        if run["judge"]:
            sp = _score_path(run_id, si, item["iid"])
            if sp.exists():
                return
            result = storage.read_json(out)
            if not result or result.get("error"):
                return
            try:
                scores = await llm.score_description(await image(item), result["description"], setup["prompt"])
            except llm.LLMError as exc:
                scores = {"error": str(exc)}
            storage.write_json(sp, scores)
            await bump("scored")

    try:
        await asyncio.gather(*(one(si, s, item) for si, s in enumerate(run["setups"]) for item in run["images"]))
        run["status"] = "cancelled" if run_id in queue.cancelled else "completed"
        run["finished"] = time.time()
    except Exception as exc:
        log.error("vlm run %s failed:\n%s", run_id, traceback.format_exc())
        run["status"] = "failed"
        run["error"] = str(exc)
    save(run)


def _avg(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return round(sum(values) / len(values), 2) if values else None


def summary(run: dict) -> dict:
    setups = []
    per_image: dict[str, list] = {}
    for si, s in enumerate(run["setups"]):
        crit = {c: [] for c in CRITERIA}
        times, failed = [], 0
        kinds: dict[str, list] = {}
        for item in run["images"]:
            out = storage.read_json(_out_path(run["id"], si, item["iid"]))
            sc = storage.read_json(_score_path(run["id"], si, item["iid"])) or {}
            if out and out.get("error"):
                failed += 1
            if out and out.get("elapsed"):
                times.append(out["elapsed"])
            for c in CRITERIA:
                if isinstance(sc.get(c), (int, float)):
                    crit[c].append(sc[c])
            if isinstance(sc.get("overall"), (int, float)):
                kinds.setdefault(item["kind"], []).append(sc["overall"])
            per_image.setdefault(item["iid"], []).append(sc.get("overall"))
        setups.append({"name": s["name"], "model": s["model"], "scores": {c: _avg(v) for c, v in crit.items()},
                       "median_time": round(statistics.median(times), 2) if times else None, "failed": failed,
                       "kinds": {k: _avg(v) for k, v in kinds.items()}})
    head = []
    for si in range(1, len(run["setups"])):
        better = worse = tie = 0
        for scores in per_image.values():
            a, b = scores[0], scores[si] if si < len(scores) else None
            if a is None or b is None:
                continue
            if b > a:
                better += 1
            elif b < a:
                worse += 1
            else:
                tie += 1
        head.append({"setup": run["setups"][si]["name"], "better": better, "worse": worse, "tie": tie})
    kinds = sorted({i["kind"] for i in run["images"]})
    counts = {k: sum(1 for i in run["images"] if i["kind"] == k) for k in kinds}
    overall = [per_image.get(i["iid"], []) for i in run["images"]]
    return {"setups": setups, "head_to_head": head, "kinds": [{"kind": k, "count": counts[k]} for k in kinds],
            "overall_by_image": overall}


def image_detail(run: dict, index: int) -> dict:
    index = max(0, min(index, len(run["images"]) - 1))
    item = run["images"][index]
    rows = []
    for si, s in enumerate(run["setups"]):
        rows.append({"setup": s["name"], "model": s["model"],
                     "out": storage.read_json(_out_path(run["id"], si, item["iid"])),
                     "scores": storage.read_json(_score_path(run["id"], si, item["iid"]))})
    return {"index": index, "total": len(run["images"]), "image": item,
            "name": PurePosixPath(item["key"]).name, "rows": rows}


def delete(run_id: str) -> None:
    import shutil
    shutil.rmtree(vdir(run_id), ignore_errors=True)
