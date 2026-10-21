"""Parser runs: one queue, every file to every parser in parallel, results kept on the PVC."""

import asyncio
import io
import logging
import shutil
import statistics
import time
import traceback
import uuid
from pathlib import PurePosixPath

from . import checks, compare, config, llm, parsers, s3store, settings, storage
from .vlm_proxy import MODES, state as proxy_state

log = logging.getLogger("parser-eval.runner")


def run_dir(run_id: str):
    return storage.data_path("runs", run_id)


def load_run(run_id: str) -> dict | None:
    return storage.read_json(run_dir(run_id) / "run.json")


def save_run(run: dict) -> None:
    storage.write_json(run_dir(run["id"]) / "run.json", run)


def result_path(run_id: str, pid: str, fid: str):
    return run_dir(run_id) / "results" / pid / f"{fid}.json"


def judge_path(run_id: str, a: str, b: str, fid: str):
    return run_dir(run_id) / "judge" / f"{a}__{b}" / f"{fid}.json"


def list_runs() -> list[dict]:
    out = []
    folder = storage.data_path("runs")
    if folder.exists():
        for sub in folder.iterdir():
            run = storage.read_json(sub / "run.json")
            if run:
                out.append({k: run.get(k) for k in (
                    "id", "name", "created", "started", "finished", "status", "dataset", "parsers", "baseline",
                    "progress", "error", "headline", "file_count")})
    out.sort(key=lambda r: r.get("created") or 0, reverse=True)
    return out


async def resolve_files(dataset: str, types: list[str], only_tested: bool) -> list[dict]:
    files = await s3store.list_recursive(dataset, max_age=30)
    all_checks = checks.all_by_fid()
    out = []
    for f in files:
        ftype = s3store.file_type(f["key"])
        if types and ftype not in types:
            continue
        fid = storage.file_id(f["key"])
        approved = [c for c in (all_checks.get(fid) or {}).get("checks", []) if c.get("status") == "approved"]
        if only_tested and not approved:
            continue
        out.append({"fid": fid, "key": f["key"], "etag": f["etag"], "size": f["size"], "type": ftype,
                    "checks": len(approved)})
    out.sort(key=lambda f: f["key"])
    return out


async def estimate(body: dict) -> dict:
    files = await resolve_files(body.get("dataset", s3store.root()), body.get("types") or [],
                                bool(body.get("only_tested")))
    selected = [body.get("baseline")] + [p for p in body.get("candidates", []) if p != body.get("baseline")]
    selected = [p for p in selected if p and parsers.get(p)]
    last = None
    for run in list_runs():
        if run.get("status") == "completed" and run.get("dataset") == body.get("dataset") and run.get("started") \
                and run.get("finished"):
            last = run["finished"] - run["started"]
            break
    return {"files": len(files), "parsers": len(selected), "requests": len(files) * len(selected),
            "checks": sum(f["checks"] for f in files), "files_with_checks": sum(1 for f in files if f["checks"]),
            "last_similar_seconds": last}


async def create_run(body: dict) -> dict:
    baseline = body.get("baseline")
    candidates = [p for p in body.get("candidates", []) if p and p != baseline]
    chosen = [baseline] + candidates if baseline else candidates
    snaps = []
    for pid in chosen:
        p = parsers.get(pid)
        if not p:
            raise ValueError(f"Parser {pid} does not exist.")
        snaps.append(parsers.snapshot(p))
    if not snaps:
        raise ValueError("Pick at least one parser.")
    dataset = body.get("dataset") or s3store.root()
    files = await resolve_files(dataset, body.get("types") or [], bool(body.get("only_tested")))
    if not files:
        raise ValueError("No files match. Check the folder and the file types.")
    vlm_mode = body.get("vlm_mode", "replay")
    if vlm_mode not in MODES:
        vlm_mode = "replay"
    judge_cfg = body.get("judge") or {}
    run_id = time.strftime("run-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    run = {
        "id": run_id,
        "name": (body.get("name") or "").strip() or f"{len(snaps)} parsers on {dataset}",
        "created": time.time(), "started": None, "finished": None, "status": "queued", "error": None,
        "dataset": dataset, "types": body.get("types") or [], "only_tested": bool(body.get("only_tested")),
        "parsers": snaps, "baseline": snaps[0]["id"],
        "vlm_mode": vlm_mode,
        "judge": {"qa": bool(judge_cfg.get("qa", True)), "pairwise": bool(judge_cfg.get("pairwise", True)),
                  "threshold": float(judge_cfg.get("threshold", settings.get("eval")["change_threshold"])),
                  "images": bool(judge_cfg.get("images", False))},
        "concurrency": max(1, int(body.get("concurrency") or 8)),
        "timeout": max(5, int(body.get("timeout") or 600)),
        "repeat": max(1, min(5, int(body.get("repeat") or 1))),
        "files": files, "file_count": len(files),
        "progress": {"done": 0, "total": len(files) * len(snaps), "phase": "queued", "judge_done": 0,
                     "judge_total": 0},
        "vlm_stats": None, "headline": None,
    }
    # Freeze the checks as they are now, so later edits do not change this run.
    for f in files:
        storage.write_json(run_dir(run_id) / "checks" / f"{f['fid']}.json", checks.approved(f["key"]))
    save_run(run)
    await queue.put(("run", run_id))
    return run


# ---------- the queue ----------

class JobQueue:
    """Runs one job at a time. Each job is itself highly parallel inside."""

    def __init__(self) -> None:
        self._q: asyncio.Queue | None = None
        self.current: str | None = None
        self.cancelled: set[str] = set()

    def _queue(self) -> asyncio.Queue:
        if self._q is None:
            self._q = asyncio.Queue()
        return self._q

    async def put(self, job: tuple[str, str]) -> None:
        await self._queue().put(job)

    def pending(self) -> int:
        return self._queue().qsize()

    async def worker(self) -> None:
        from . import vlm_eval  # local import, avoids a cycle
        while True:
            kind, job_id = await self._queue().get()
            self.current = job_id
            try:
                if job_id in self.cancelled:
                    continue
                if kind == "run":
                    await execute(job_id)
                elif kind == "vlm":
                    await vlm_eval.execute(job_id)
            except Exception:
                log.error("job %s failed:\n%s", job_id, traceback.format_exc())
            finally:
                self.current = None
                self._queue().task_done()


queue = JobQueue()


def recover_on_start() -> list[str]:
    """Runs that were going when the pod stopped are marked interrupted, ready to resume."""
    changed = []
    folder = storage.data_path("runs")
    if folder.exists():
        for sub in folder.iterdir():
            run = storage.read_json(sub / "run.json")
            if run and run.get("status") in ("running", "queued"):
                run["status"] = "interrupted"
                run["progress"]["phase"] = "interrupted"
                save_run(run)
                changed.append(run["id"])
    return changed


async def resume(run_id: str) -> dict:
    run = load_run(run_id)
    if not run:
        raise KeyError(run_id)
    if run["status"] in ("running", "queued"):
        return run
    queue.cancelled.discard(run_id)
    run["status"] = "queued"
    run["error"] = None
    run["progress"]["phase"] = "queued"
    save_run(run)
    await queue.put(("run", run_id))
    return run


def cancel(run_id: str) -> None:
    queue.cancelled.add(run_id)
    run = load_run(run_id)
    if run and run["status"] == "queued":
        run["status"] = "cancelled"
        save_run(run)


def delete_run(run_id: str) -> None:
    cancel(run_id)
    shutil.rmtree(run_dir(run_id), ignore_errors=True)


# ---------- execution ----------

def _page_images(data: bytes, key: str, max_pages: int = 8) -> list[bytes]:
    ext = PurePosixPath(key).suffix.lower()
    if s3store.file_type(key) == "Images":
        return [data]
    if ext != ".pdf":
        return []
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(data)
        out = []
        for i in range(min(len(pdf), max_pages)):
            img = pdf[i].render(scale=1.4).to_pil()
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=85)
            out.append(buf.getvalue())
        return out
    except Exception:
        return []


async def _evaluate_checks(run: dict, fid: str, text: str, ok: bool) -> list[dict]:
    frozen = storage.read_json(run_dir(run["id"]) / "checks" / f"{fid}.json", []) or []
    ev = settings.get("eval")
    out = []
    for c in frozen:
        item = {"id": c["id"], "kind": c["kind"], "desc": checks.describe(c)}
        if not ok:
            item.update(passed=False, detail="parser returned an error")
        elif c["kind"] == "question":
            if not run["judge"]["qa"]:
                item.update(passed=None, detail="question checks were off for this run")
            else:
                try:
                    answer = await llm.answer_question(text, c.get("question", ""))
                    grade = await llm.grade_answer(c.get("question", ""), c.get("answer", ""), answer)
                    item.update(passed=grade["correct"], detail=grade["reason"], answer=answer)
                except llm.LLMError as exc:
                    item.update(passed=None, detail=f"judge error: {exc}")
        else:
            item.update(await asyncio.to_thread(checks.evaluate, c, text, image_tag=ev["image_tag"],
                                                fuzzy_threshold=int(ev["fuzzy_threshold"])))
        out.append(item)
    return out


async def execute(run_id: str) -> None:
    run = load_run(run_id)
    if not run:
        return
    run.update(status="running", started=run.get("started") or time.time(), error=None)
    run["progress"]["phase"] = "parsing"
    save_run(run)
    proxy_state.mode, proxy_state.owner = run["vlm_mode"], run_id
    calls0, hits0 = proxy_state.calls, proxy_state.hits
    work = config.WORK_DIR / run_id
    work.mkdir(parents=True, exist_ok=True)
    locks: dict[str, asyncio.Lock] = {}
    files = {f["fid"]: f for f in run["files"]}
    progress_lock = asyncio.Lock()
    done = sum(1 for p in run["parsers"] for fid in files if result_path(run_id, p["id"], fid).exists())
    run["progress"]["done"] = done

    async def file_bytes(fid: str) -> bytes:
        lock = locks.setdefault(fid, asyncio.Lock())
        async with lock:
            path = work / fid
            if not path.exists():
                path.write_bytes(await s3store.get_bytes(files[fid]["key"]))
            return path.read_bytes()

    async def bump(field: str = "done") -> None:
        async with progress_lock:
            run["progress"][field] += 1
            save_run(run)

    async def one(p: dict, fid: str, sem: asyncio.Semaphore) -> None:
        if run_id in queue.cancelled:
            return
        out_path = result_path(run_id, p["id"], fid)
        if out_path.exists():
            return
        f = files[fid]
        live = parsers.get(p["id"]) or p  # live config carries the token
        live = {**live, "timeout": run["timeout"]}
        try:
            data = await file_bytes(fid)
        except s3store.S3Error as exc:
            res = {"ok": False, "error": str(exc), "text": "", "elapsed": 0, "size": 0, "status": None}
            times = []
        else:
            times, res = [], None
            async with sem:
                for _ in range(run["repeat"]):
                    if run_id in queue.cancelled:
                        return
                    attempt = await parsers.call(live, PurePosixPath(f["key"]).name, data,
                                                 s3store.content_type(f["key"]))
                    times.append(attempt["elapsed"])
                    if res is None or (attempt["ok"] and not res["ok"]):
                        res = attempt
        results = await _evaluate_checks(run, fid, res["text"], res["ok"])
        evaluated = [c for c in results if c["passed"] is not None]
        storage.write_json(out_path, {
            "pid": p["id"], "fid": fid, "key": f["key"], "type": f["type"],
            "ok": res["ok"], "error": res.get("error"), "status": res.get("status"),
            "elapsed": round(statistics.median(times), 3) if times else res.get("elapsed", 0), "times": times,
            "size": res.get("size", 0), "chars": len(res["text"]),
            "image_blocks": len(checks.image_blocks(res["text"], settings.get("eval")["image_tag"])),
            "checks": results, "passed": sum(1 for c in evaluated if c["passed"]), "evaluated": len(evaluated),
            "text": res["text"], "finished": time.time(),
        })
        _record_last(fid, run, p, res["ok"], sum(1 for c in evaluated if c["passed"]), len(evaluated))
        await bump()

    try:
        tasks = []
        for p in run["parsers"]:
            sem = asyncio.Semaphore(min(run["concurrency"], int(p.get("parallel") or run["concurrency"])))
            tasks += [one(p, fid, sem) for fid in files]
        await asyncio.gather(*tasks)

        if run_id not in queue.cancelled and run["judge"]["pairwise"] and len(run["parsers"]) > 1:
            run["progress"]["phase"] = "judging"
            save_run(run)
            await _pairwise_phase(run, files, file_bytes, bump)

        if run_id in queue.cancelled:
            run["status"] = "cancelled"
        else:
            run["status"] = "completed"
            run["finished"] = time.time()
        run["progress"]["phase"] = run["status"]
    except Exception as exc:
        log.error("run %s failed:\n%s", run_id, traceback.format_exc())
        run["status"] = "failed"
        run["error"] = str(exc)
        run["progress"]["phase"] = "failed"
    finally:
        run["vlm_stats"] = {"calls": proxy_state.calls - calls0, "hits": proxy_state.hits - hits0}
        proxy_state.mode, proxy_state.owner = "replay", None
        shutil.rmtree(work, ignore_errors=True)
        try:
            if len(run["parsers"]) > 1 and run["status"] == "completed":
                s = compare.summary(run, run["parsers"][0]["id"], run["parsers"][1]["id"])
                run["headline"] = {"improved": s["counts"]["improved"], "regressed": s["counts"]["regressed"]}
            elif run["status"] == "completed":
                s = compare.single_summary(run, run["parsers"][0]["id"])
                run["headline"] = {"passed": s["passed"], "evaluated": s["evaluated"]}
        except Exception:
            log.warning("could not compute headline for %s", run_id)
        save_run(run)


def _record_last(fid: str, run: dict, p: dict, ok: bool, passed: int, evaluated: int) -> None:
    """Latest result per file, shown in Files & tests. The last parser of a run wins (the candidate)."""
    path = storage.data_path("outputs", "last-results.json")
    data = storage.read_json(path, {}) or {}
    current = data.get(fid)
    order = [x["id"] for x in run["parsers"]]
    if current and current.get("run_id") == run["id"] and current.get("pid") in order \
            and order.index(current["pid"]) > order.index(p["id"]):
        return
    data[fid] = {"run_id": run["id"], "pid": p["id"], "parser": p["name"], "ok": ok, "passed": passed,
                 "evaluated": evaluated, "at": time.time()}
    storage.write_json(path, data)


async def _pairwise_phase(run, files, file_bytes, bump) -> None:
    run_id = run["id"]
    base = run["parsers"][0]["id"]
    threshold = run["judge"]["threshold"]
    use_images = run["judge"]["images"] and settings.get("judge")["vision"]
    jobs = []
    for cand in run["parsers"][1:]:
        for fid in files:
            out = judge_path(run_id, base, cand["id"], fid)
            if out.exists():
                continue
            a = storage.read_json(result_path(run_id, base, fid))
            b = storage.read_json(result_path(run_id, cand["id"], fid))
            if not a or not b or not a["ok"] or not b["ok"]:
                continue
            change = await asyncio.to_thread(compare.change_percent, a["text"], b["text"])
            if change <= threshold:
                continue
            jobs.append((cand["id"], fid, a, b, out))
    run["progress"]["judge_total"] = len(jobs)
    run["progress"]["judge_done"] = 0
    save_run(run)

    async def judge_one(cand_id, fid, a, b, out):
        if run_id in queue.cancelled:
            return
        images = None
        if use_images:
            try:
                images = await asyncio.to_thread(_page_images, await file_bytes(fid), files[fid]["key"])
            except Exception:
                images = None
        try:
            verdict = await llm.pairwise_both_orders(a["text"], b["text"], PurePosixPath(files[fid]["key"]).name,
                                                     images)
        except llm.LLMError as exc:
            verdict = {"verdict": "error", "agree": False, "reason": str(exc)}
        storage.write_json(out, verdict)
        await bump("judge_done")

    await asyncio.gather(*(judge_one(*j) for j in jobs))


def cleanup_old_runs() -> int:
    days = int(settings.get("storage")["keep_days"] or 0)
    if days <= 0:
        return 0
    limit = time.time() - days * 86400
    removed = 0
    for run in list_runs():
        if (run.get("created") or 0) < limit and run.get("status") not in ("running", "queued"):
            shutil.rmtree(run_dir(run["id"]), ignore_errors=True)
            removed += 1
    return removed
