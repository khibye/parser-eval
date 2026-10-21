"""Checks: small pass/fail assertions about a parser's output for one file."""

import re
import time
import unicodedata
import uuid
from typing import Any

from rapidfuzz import fuzz

from . import storage

KINDS = {
    "contains": "Contains",
    "not_contains": "Not contains",
    "order": "Order",
    "appears_once": "Appears once",
    "image_blocks": "Image blocks",
    "image_contains": "Image contains",
    "table_cell": "Table cell",
    "question": "Question",
}

# Niqqud, cantillation and other Hebrew points (letters are U+05D0..U+05EA and stay).
_HEB_MARKS = re.compile(r"[֑-ׇֽֿׁׂׅׄ]")
_BIDI = re.compile(r"[‎‏‪-‮⁦-⁩؜﻿​-‍]")
_MD_EMPHASIS = re.compile(r"(\*\*|__|`)")
_SPACES = re.compile(r"\s+")
_TRANSLATE = str.maketrans({
    "׳": "'", "״": '"', "‘": "'", "’": "'", "“": '"', "”": '"',
    "־": "-", "–": "-", "—": "-", " ": " ",
})


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = _HEB_MARKS.sub("", text)
    text = _BIDI.sub("", text)
    text = text.translate(_TRANSLATE)
    text = _MD_EMPHASIS.sub("", text)
    text = _SPACES.sub(" ", text)
    return text.strip().lower()


def image_blocks(text: str, tag: str) -> list[str]:
    pattern = re.compile(rf"<{re.escape(tag)}\b[^>]*>(.*?)</{re.escape(tag)}>", re.S | re.I)
    return pattern.findall(text or "")


def _contains(needle: str, haystack: str, fuzzy: bool, threshold: int) -> tuple[bool, float]:
    n, h = normalize(needle), haystack
    if not n:
        return True, 100.0
    if n in h:
        return True, 100.0
    if not fuzzy:
        return False, 0.0
    score = fuzz.partial_ratio(n, h, score_cutoff=0)
    return score >= threshold, round(score, 1)


def _find_pos(needle: str, haystack: str, start: int, threshold: int) -> int:
    n = normalize(needle)
    idx = haystack.find(n, start)
    if idx >= 0:
        return idx
    align = fuzz.partial_ratio_alignment(n, haystack[start:], score_cutoff=threshold)
    if align is not None:
        return start + align.dest_start
    return -1


def evaluate(check: dict, text: str, *, image_tag: str, fuzzy_threshold: int) -> dict:
    """Runs one deterministic check. Question checks are handled by the judge."""
    kind = check.get("kind")
    norm = normalize(text)
    fuzzy = check.get("match", "fuzzy") == "fuzzy"
    threshold = int(check.get("threshold") or fuzzy_threshold)
    try:
        if kind == "contains":
            ok, score = _contains(check.get("text", ""), norm, fuzzy, threshold)
            return {"passed": ok, "detail": "found" if score == 100 else f"best match {score}%"}
        if kind == "not_contains":
            found = normalize(check.get("text", "")) in norm
            return {"passed": not found, "detail": "found in output" if found else "not found"}
        if kind == "appears_once":
            n = normalize(check.get("text", ""))
            count = norm.count(n) if n else 0
            return {"passed": count == 1, "detail": f"found {count} times"}
        if kind == "order":
            items = [i for i in check.get("items", []) if str(i).strip()]
            pos = 0
            for i, item in enumerate(items):
                found = _find_pos(item, norm, pos, threshold)
                if found < 0:
                    if _find_pos(item, norm, 0, threshold) >= 0:
                        return {"passed": False, "detail": f"“{item}” appears before “{items[i - 1]}”"}
                    return {"passed": False, "detail": f"“{item}” not found"}
                pos = found + 1
            return {"passed": True, "detail": "in order"}
        if kind == "image_blocks":
            count = len(image_blocks(text, image_tag))
            expected = int(check.get("count", 0))
            if check.get("op", "eq") == "min":
                return {"passed": count >= expected, "detail": f"found {count}"}
            return {"passed": count == expected, "detail": f"found {count}"}
        if kind == "image_contains":
            blocks = image_blocks(text, image_tag)
            index = int(check.get("index", 1))
            if index < 1 or index > len(blocks):
                return {"passed": False, "detail": f"only {len(blocks)} image blocks"}
            ok, score = _contains(check.get("text", ""), normalize(blocks[index - 1]), fuzzy, threshold)
            return {"passed": ok, "detail": "found" if score == 100 else f"best match {score}%"}
        if kind == "table_cell":
            row, value = normalize(check.get("row", "")), normalize(check.get("value", ""))
            for line in (text or "").splitlines():
                nl = normalize(line)
                if row and row in nl and value in nl.replace(row, "", 1):
                    return {"passed": True, "detail": "found in one row"}
            return {"passed": False, "detail": "no row has both"}
        if kind == "question":
            return {"passed": None, "detail": "needs the judge"}
    except Exception as exc:  # a malformed check must not stop a run
        return {"passed": None, "detail": f"check error: {exc}"}
    return {"passed": None, "detail": f"unknown check type {kind}"}


def describe(check: dict) -> str:
    kind = check.get("kind")
    if kind in ("contains", "not_contains", "appears_once"):
        return f"“{check.get('text', '')}”"
    if kind == "order":
        return " → ".join(f"“{i}”" for i in check.get("items", []))
    if kind == "image_blocks":
        return f"{'at least' if check.get('op') == 'min' else 'exactly'} {check.get('count')}"
    if kind == "image_contains":
        return f"image {check.get('index')}: “{check.get('text', '')}”"
    if kind == "table_cell":
        return f"row “{check.get('row', '')}”, value “{check.get('value', '')}”"
    if kind == "question":
        return f"{check.get('question', '')} → {check.get('answer', '')}"
    return str(check)


# ---------- storage of checks per S3 file ----------

def _path(key: str):
    return storage.data_path("checks", f"{storage.file_id(key)}.json")


def load(key: str) -> dict:
    return storage.read_json(_path(key)) or {"key": key, "etag": None, "checks": []}


def approved(key: str) -> list[dict]:
    return [c for c in load(key)["checks"] if c.get("status", "approved") == "approved"]


def clean_check(raw: dict) -> dict:
    kind = raw.get("kind")
    if kind not in KINDS:
        raise ValueError(f"Unknown check type: {kind}")
    out: dict[str, Any] = {"id": raw.get("id") or uuid.uuid4().hex[:10], "kind": kind,
                           "status": raw.get("status", "approved")}
    if out["status"] not in ("approved", "draft"):
        out["status"] = "approved"
    for field in ("text", "question", "answer", "row", "value", "match", "op", "source"):
        if raw.get(field) not in (None, ""):
            out[field] = str(raw[field])
    if kind == "order":
        out["items"] = [str(i) for i in raw.get("items", []) if str(i).strip()]
    if kind in ("image_blocks",):
        out["count"] = int(raw.get("count", 0))
    if kind == "image_contains":
        out["index"] = int(raw.get("index", 1))
    if raw.get("threshold"):
        out["threshold"] = int(raw["threshold"])
    return out


def save(key: str, etag: str | None, checks: list[dict]) -> dict:
    data = {"key": key, "etag": etag, "updated": time.time(), "checks": [clean_check(c) for c in checks]}
    storage.write_json(_path(key), data)
    return data


def all_by_fid() -> dict[str, dict]:
    out = {}
    folder = storage.data_path("checks")
    if folder.exists():
        for path in folder.glob("*.json"):
            data = storage.read_json(path)
            if data and data.get("key"):
                out[path.stem] = data
    return out
