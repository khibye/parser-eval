"""Uploads through the API, against an in-memory S3 (moto)."""

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import auth, config, s3store, settings, storage
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "APP_INITIAL_PASSWORD", "test-password-1")
    monkeypatch.setattr(settings, "_cache", None)
    monkeypatch.setattr(s3store, "_client", None)
    s3store.invalidate_cache()
    storage.ensure_dirs()
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="eval-corpus")
        from app.main import app
        c = TestClient(app)
        assert c.post("/api/login", json={"password": "test-password-1"}).status_code == 200
        yield c


def set_root(client, root):
    r = client.put("/api/settings/s3", json={"endpoint": "https://s3.amazonaws.com", "bucket": "eval-corpus",
                                             "root": root, "access_key": "test", "secret_key": "test"})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("root,prefix", [("", ""), ("", "real/"), ("corpus/", "corpus/"), ("corpus/", "corpus/real")])
def test_upload(client, root, prefix):
    set_root(client, root)
    r = client.post("/api/files/upload", data={"prefix": prefix},
                    files=[("files", ("a.pdf", b"%PDF-1.4 test", "application/pdf"))])
    assert r.status_code == 200, r.text
    expected = (prefix if not prefix or prefix.endswith("/") else prefix + "/") + "a.pdf"
    assert r.json()["saved"] == [expected]
    listing = client.get("/api/files", params={"prefix": expected.rsplit("/", 1)[0] + "/" if "/" in expected else ""})
    assert any(f["key"] == expected for f in listing.json()["files"])


def test_upload_without_prefix_field_goes_to_root(client):
    set_root(client, "corpus/")
    r = client.post("/api/files/upload", files=[("files", ("b.docx", b"x", "application/octet-stream"))])
    assert r.status_code == 200, r.text
    assert r.json()["saved"] == ["corpus/b.docx"]


def test_upload_outside_root_is_refused(client):
    set_root(client, "corpus/")
    r = client.post("/api/files/upload", data={"prefix": "other/"},
                    files=[("files", ("c.pdf", b"x", "application/pdf"))])
    assert r.status_code == 400
