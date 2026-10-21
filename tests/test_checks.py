from app import checks, compare

TEXT = """# טופס 101 · כרטיס עובד
שנת מס 2026
## א. פרטי המעסיק
| שם | חברת דוגמה בע״מ |
## ב. פרטי העובד
| שם משפחה | כהן |
<image_analysis index="1">
OCR: מכירות לפי אזור · צפון 4.2
VLM: Bar chart
</image_analysis>
## ח. הצהרת העובד
אני מצהיר כי הפרטים מלאים ונכונים.
"""


def ev(check):
    return checks.evaluate(check, TEXT, image_tag="image_analysis", fuzzy_threshold=90)


def test_contains_exact_and_niqqud():
    assert ev({"kind": "contains", "text": "שנת מס 2026"})["passed"]
    # niqqud and a different geresh character are normalized away
    assert ev({"kind": "contains", "text": "חֶבְרַת דוגמה בע\"מ"})["passed"]


def test_contains_fuzzy_and_exact_miss():
    assert ev({"kind": "contains", "text": "שנת מס 2025", "match": "fuzzy"})["passed"]
    assert not ev({"kind": "contains", "text": "שנת מס 2025", "match": "exact"})["passed"]


def test_not_contains_and_once():
    assert ev({"kind": "not_contains", "text": "<!-- image -->"})["passed"]
    assert ev({"kind": "appears_once", "text": "פרטי העובד"})["passed"]
    assert not ev({"kind": "appears_once", "text": "פרטי"})["passed"]


def test_order():
    assert ev({"kind": "order", "items": ["פרטי המעסיק", "פרטי העובד", "הצהרת העובד"]})["passed"]
    res = ev({"kind": "order", "items": ["הצהרת העובד", "פרטי המעסיק"]})
    assert not res["passed"] and "before" in res["detail"]


def test_images_and_table():
    assert ev({"kind": "image_blocks", "count": 1})["passed"]
    assert not ev({"kind": "image_blocks", "count": 2})["passed"]
    assert ev({"kind": "image_contains", "index": 1, "text": "מכירות לפי אזור"})["passed"]
    assert ev({"kind": "table_cell", "row": "שם משפחה", "value": "כהן"})["passed"]
    assert not ev({"kind": "table_cell", "row": "שם משפחה", "value": "לוי"})["passed"]


def test_question_needs_judge():
    assert ev({"kind": "question", "question": "?", "answer": "x"})["passed"] is None


def test_change_percent_and_diff():
    assert compare.change_percent(TEXT, TEXT) == 0
    changed = TEXT.replace("## ח. הצהרת העובד\n", "")
    assert 0 < compare.change_percent(TEXT, changed) < 20
    rows = compare.diff_rows(TEXT, changed)
    assert any(r["kind"] == "del" for r in rows)


def test_form_fields_and_text_path():
    from app import parsers
    p = {"params": {"to_formats": ["md", "json"], "do_ocr": True, "ocr_lang": ["heb"], "x": None, "opts": {"a": 1}}}
    f = parsers._form_fields(p)
    assert f["to_formats"] == ["md", "json"] and f["do_ocr"] == "true" and "x" not in f and f["opts"] == '{"a": 1}'
    f = parsers._form_fields({**p, "list_mode": "json"})
    assert f["to_formats"] == '["md", "json"]'
    assert parsers.extract_text({"document": {"md_content": "hi"}}, "document.md_content") == "hi"
    assert parsers.extract_text([{"document": {"md_content": "x"}}], "document.md_content") == "x"
    assert parsers.extract_text({"document": {}}, "document.md_content") is None


def test_secrets_are_encrypted(tmp_path, monkeypatch):
    from app import config, settings, storage
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "_cache", None)
    storage.ensure_dirs()
    settings.update("judge", {"api_key": "sk-very-secret", "model": "m"})
    raw = (tmp_path / "config" / "settings.json").read_text()
    assert "sk-very-secret" not in raw
    assert settings.get("judge")["api_key"] == "sk-very-secret"
    assert settings.public("judge")["api_key"] == {"set": True}
    settings.update("judge", {"model": "m2"})  # a missing secret keeps the stored one
    assert settings.get("judge")["api_key"] == "sk-very-secret"
