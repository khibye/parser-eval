"""Comparing two parsers inside one run."""

import difflib
import hashlib
import statistics

from . import storage


def _lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


_change_cache: dict[tuple[str, str], float] = {}


def change_percent(a: str, b: str) -> float:
    """How much of the text changed, by lines. 0 means identical."""
    a, b = a or "", b or ""
    key = (hashlib.sha1(a.encode()).hexdigest(), hashlib.sha1(b.encode()).hexdigest())
    if key in _change_cache:
        return _change_cache[key]
    la, lb = _lines(a), _lines(b)
    if not la and not lb:
        value = 0.0
    else:
        value = round((1 - difflib.SequenceMatcher(None, la, lb, autojunk=False).ratio()) * 100, 1)
    if len(_change_cache) > 20000:
        _change_cache.clear()
    _change_cache[key] = value
    return value


def _result(run_id: str, pid: str, fid: str) -> dict | None:
    from .runner import result_path
    return storage.read_json(result_path(run_id, pid, fid))


def _judge(run_id: str, a: str, b: str, fid: str) -> dict | None:
    from .runner import judge_path
    return storage.read_json(judge_path(run_id, a, b, fid))


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    idx = min(len(values) - 1, max(0, int(round(q * (len(values) - 1)))))
    return round(values[idx], 2)


def _judge_label(j: dict | None) -> str:
    if not j:
        return ""
    v = j.get("verdict")
    if v == "error":
        return "Judge error"
    if v == "disagree":
        return "Orders disagree"
    if v == "tie":
        return "Tie"
    return f"{v} better" + (", both orders" if j.get("agree") else ", one order")


def file_row(run: dict, a: str, b: str, f: dict, threshold: float) -> dict:
    ra, rb = _result(run["id"], a, f["fid"]), _result(run["id"], b, f["fid"])
    row = {"fid": f["fid"], "key": f["key"], "type": f["type"], "a_ok": bool(ra and ra["ok"]),
           "b_ok": bool(rb and rb["ok"]), "missing": ra is None or rb is None}
    if row["missing"]:
        row.update(status="pending", notes=["not parsed yet"])
        return row
    row.update(
        a_passed=ra["passed"], a_eval=ra["evaluated"], b_passed=rb["passed"], b_eval=rb["evaluated"],
        a_time=ra["elapsed"], b_time=rb["elapsed"], a_error=ra.get("error"), b_error=rb.get("error"),
        total_checks=len(rb["checks"]),
        change=change_percent(ra["text"], rb["text"]) if (ra["ok"] and rb["ok"]) else None,
    )
    j = _judge(run["id"], a, b, f["fid"])
    row["judge"] = j.get("verdict") if j else None
    row["judge_label"] = _judge_label(j) if j else (
        "Not run, under threshold" if row["change"] is not None and row["change"] <= threshold else "")
    notes = []
    if not row["a_ok"] and row["b_ok"]:
        status = "improved"
        notes.append(f"A failed: {ra.get('error')}")
    elif row["a_ok"] and not row["b_ok"]:
        status = "regressed"
        notes.append(f"B failed: {rb.get('error')}")
    elif not row["a_ok"] and not row["b_ok"]:
        status = "unchanged"
        notes.append("Both failed")
    elif rb["passed"] < ra["passed"]:
        status = "regressed"
    elif rb["passed"] > ra["passed"]:
        status = "improved"
    else:
        status = "unchanged"
    by_id_a = {c["id"]: c for c in ra["checks"]}
    for c in rb["checks"]:
        ca = by_id_a.get(c["id"])
        if ca and ca["passed"] and c["passed"] is False:
            notes.append(f"now fails · {c['kind']}: {c['desc']} ({c['detail']})")
        elif ca and ca["passed"] is False and c["passed"]:
            notes.append(f"now passes · {c['kind']}: {c['desc']}")
    row["status"] = status
    row["needs_review"] = status == "unchanged" and row["change"] is not None and row["change"] > threshold
    row["notes"] = notes[:6]
    return row


def summary(run: dict, a: str, b: str) -> dict:
    threshold = run.get("judge", {}).get("threshold", 15)
    rows = [file_row(run, a, b, f, threshold) for f in run["files"]]
    done = [r for r in rows if not r["missing"]]
    counts = {"improved": sum(r["status"] == "improved" for r in done),
              "regressed": sum(r["status"] == "regressed" for r in done),
              "unchanged": sum(r["status"] == "unchanged" for r in done),
              "needs_review": sum(bool(r.get("needs_review")) for r in done),
              "pending": len(rows) - len(done)}
    a_pass = sum(r["a_passed"] for r in done)
    b_pass = sum(r["b_passed"] for r in done)
    total = sum(max(r["a_eval"], r["b_eval"]) for r in done)
    a_times = [r["a_time"] for r in done if r["a_ok"]]
    b_times = [r["b_time"] for r in done if r["b_ok"]]
    judged = [r["judge"] for r in done if r.get("judge")]
    by_type: dict[str, dict] = {}
    for r in done:
        t = by_type.setdefault(r["type"], {"type": r["type"], "files": 0, "a": 0, "b": 0, "total": 0, "up": 0,
                                           "down": 0, "a_times": [], "b_times": [], "a_err": 0, "b_err": 0})
        t["files"] += 1
        t["a"] += r["a_passed"]
        t["b"] += r["b_passed"]
        t["total"] += max(r["a_eval"], r["b_eval"])
        t["up"] += r["status"] == "improved"
        t["down"] += r["status"] == "regressed"
        if r["a_ok"]:
            t["a_times"].append(r["a_time"])
        else:
            t["a_err"] += 1
        if r["b_ok"]:
            t["b_times"].append(r["b_time"])
        else:
            t["b_err"] += 1
    types = []
    for t in sorted(by_type.values(), key=lambda x: x["type"]):
        t["a_median"] = round(statistics.median(t["a_times"]), 2) if t["a_times"] else None
        t["b_median"] = round(statistics.median(t["b_times"]), 2) if t["b_times"] else None
        del t["a_times"], t["b_times"]
        types.append(t)
    return {
        "a": a, "b": b, "rows": rows, "counts": counts, "by_type": types,
        "checks": {"a": a_pass, "b": b_pass, "total": total},
        "time": {"a_median": round(statistics.median(a_times), 2) if a_times else None,
                 "b_median": round(statistics.median(b_times), 2) if b_times else None,
                 "a_p95": _pct(a_times, 0.95), "b_p95": _pct(b_times, 0.95)},
        "errors": {"a": sum(not r["a_ok"] for r in done), "b": sum(not r["b_ok"] for r in done)},
        "judge": {"A": judged.count("A"), "B": judged.count("B"), "tie": judged.count("tie"),
                  "disagree": judged.count("disagree"), "error": judged.count("error"), "total": len(judged)},
        "threshold": threshold,
    }


def single_summary(run: dict, pid: str) -> dict:
    passed = evaluated = errors = 0
    times = []
    for f in run["files"]:
        r = _result(run["id"], pid, f["fid"])
        if not r:
            continue
        passed += r["passed"]
        evaluated += r["evaluated"]
        if r["ok"]:
            times.append(r["elapsed"])
        else:
            errors += 1
    return {"passed": passed, "evaluated": evaluated, "errors": errors,
            "median": round(statistics.median(times), 2) if times else None}


def diff_rows(text_a: str, text_b: str, only_changes: bool = True, context: int = 2) -> list[dict]:
    """Side-by-side rows: same, changed, removed (only in A) and added (only in B)."""
    la = (text_a or "").splitlines()
    lb = (text_b or "").splitlines()
    sm = difflib.SequenceMatcher(None, la, lb, autojunk=False)
    rows: list[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            block = [{"kind": "same", "an": i1 + k + 1, "a": la[i1 + k], "bn": j1 + k + 1, "b": lb[j1 + k]}
                     for k in range(i2 - i1)]
            if only_changes and len(block) > context * 2 + 1:
                hidden = len(block) - context * 2
                block = block[:context] + [{"kind": "skip", "count": hidden}] + block[-context:]
            rows.extend(block)
        else:
            n = max(i2 - i1, j2 - j1)
            for k in range(n):
                a_line = la[i1 + k] if i1 + k < i2 else None
                b_line = lb[j1 + k] if j1 + k < j2 else None
                kind = "change" if (a_line is not None and b_line is not None) else ("del" if b_line is None else "add")
                rows.append({"kind": kind, "an": i1 + k + 1 if a_line is not None else None, "a": a_line,
                             "bn": j1 + k + 1 if b_line is not None else None, "b": b_line})
    if len(rows) > 6000:
        rows = rows[:6000] + [{"kind": "skip", "count": len(rows) - 6000}]
    return rows


def file_detail(run: dict, a: str, b: str | None, fid: str, only_changes: bool = True) -> dict:
    f = next((x for x in run["files"] if x["fid"] == fid), None)
    if not f:
        raise KeyError(fid)
    ra = _result(run["id"], a, fid)
    rb = _result(run["id"], b, fid) if b else None
    out = {"file": f, "a": ra, "b": rb}
    if ra and rb:
        out["diff"] = diff_rows(ra["text"], rb["text"], only_changes)
        out["change"] = change_percent(ra["text"], rb["text"]) if ra["ok"] and rb["ok"] else None
        out["judge"] = _judge(run["id"], a, b, fid)
        out["row"] = file_row(run, a, b, f, run.get("judge", {}).get("threshold", 15))
    return out
