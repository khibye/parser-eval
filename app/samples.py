"""The bundled synthetic test set: files with ready-made checks, imported into S3 in one click."""

from . import checks, config, s3store, storage


def manifest() -> list[dict]:
    data = storage.read_json(config.SAMPLES_DIR / "manifest.json", []) or []
    return [{"path": item["path"], "description": item.get("description", ""), "checks": len(item.get("checks", []))}
            for item in data]


async def import_to_s3(prefix: str) -> dict:
    if not prefix.endswith("/"):
        prefix += "/"
    items = storage.read_json(config.SAMPLES_DIR / "manifest.json", []) or []
    imported = []
    for item in items:
        path = config.SAMPLES_DIR / "files" / item["path"]
        key = prefix + item["path"]
        await s3store.put(key, path.read_bytes())
        meta = await s3store.head(key)
        checks.save(key, meta["etag"] if meta else None, item.get("checks", []))
        imported.append(key)
    return {"imported": len(imported), "prefix": prefix}
